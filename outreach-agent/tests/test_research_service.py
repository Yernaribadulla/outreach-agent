"""Loopback-only contract checks: no live websites, LM Studio or SMTP."""
import json
import os
import socket
import threading
import unittest
from http.client import HTTPConnection
from unittest.mock import patch

from app.research.client import ResearchUnavailable, request_research
from app.research.network import PublicRedirectHandler, validate_public_url
from app.research.server import ResearchServer
from app.website_audit import audit_website
from app.extraction.public_page import inspect_site


class ResearchServiceTests(unittest.TestCase):
    def setUp(self):
        self.server = ResearchServer(('127.0.0.1', 0), token='test-token', concurrency=1)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path, raw=b'{}', headers=None, method='POST'):
        conn = HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        base_headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer test-token'}
        base_headers.update(headers or {})
        conn.request(method, path, raw, base_headers)
        response = conn.getresponse()
        status, data = response.status, json.loads(response.read())
        conn.close()
        return status, data

    def test_health(self):
        status, data = self.request('/healthz', method='GET')
        self.assertEqual(status, 200)
        self.assertEqual(data['version'], 1)

    def test_token_and_bind(self):
        self.assertEqual(self.request('/v1/audit', headers={'Authorization': 'wrong'})[0], 401)
        with self.assertRaises(ValueError):
            ResearchServer(('0.0.0.0', 0))

    def test_bad_json_and_body_limits(self):
        for raw, expected in [(b'{broken', 400), (b'[]', 400), (b'null', 400), (b'\xff', 400), (b'{}' * 5000, 413)]:
            with self.subTest(raw=raw[:10]):
                self.assertEqual(self.request('/v1/audit', raw)[0], expected)
        self.assertEqual(self.request('/v1/audit', headers={'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(self.request('/v1/audit', headers={'Origin': 'https://other.test'})[0], 403)
        self.assertEqual(self.request('/v1/audit', headers={'Origin': 'http://['})[0], 403)

    def test_invalid_contract_inputs(self):
        for data in [{'url': 'file:///etc/passwd'}, {'url': 'http://user:pass@clinic.test'}, {'url': ''}, {'url': []}, {'url': 'https://clinic.test', 'timeout': True}, {'url': 'https://clinic.test', 'timeout': 99}]:
            with self.subTest(data=data):
                self.assertEqual(self.request('/v1/audit', json.dumps(data).encode())[0], 400)
        self.assertEqual(self.request('/missing')[0], 404)

    def test_capacity_rejects_without_fetching(self):
        self.server.work_slots.acquire()
        try:
            with patch('app.research.server.audit_website_local') as audit:
                self.assertEqual(self.request('/v1/audit', b'{"url":"https://clinic.test"}')[0], 503)
                audit.assert_not_called()
        finally:
            self.server.work_slots.release()

    def test_audit_round_trip_preserves_provenance(self):
        html = '<title>Clinic</title><meta name="viewport"><a href="/booking">Book online</a>'
        env = {'RESEARCH_SERVICE_URL': self.base, 'RESEARCH_SERVICE_TOKEN': 'test-token'}
        with patch.dict(os.environ, env), patch('app.website_audit._fetch', return_value=(html, 'https://clinic.test/', 200, .1)) as fetch:
            result = audit_website('https://clinic.test')
        fetch.assert_called_once_with('https://clinic.test', timeout=8)
        self.assertTrue(result['signals']['booking'])
        self.assertEqual(result['source'], 'https://clinic.test/')
        self.assertEqual(result['status'], 'CONFIRMED')

    def test_inspection_round_trip(self):
        html = '<a href="mailto:info@clinic.test">Email</a>'
        env = {'RESEARCH_SERVICE_URL': self.base, 'RESEARCH_SERVICE_TOKEN': 'test-token'}
        with patch.dict(os.environ, env), patch('app.extraction.public_page.fetch', return_value=(html, 'https://clinic.test/contact')):
            result = inspect_site('https://clinic.test')
        self.assertEqual(len(result['contacts']), 1)
        self.assertEqual(result['contacts'][0]['source_url'], 'https://clinic.test/contact')

    def test_outage_is_unknown_without_local_fallback(self):
        with patch('app.research.client.service_url', return_value=self.base), patch('app.research.client.urlopen', side_effect=OSError('secret detail')), patch('app.website_audit._fetch') as local:
            result = audit_website('https://clinic.test')
            inspected = inspect_site('https://clinic.test')
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertTrue(all(item['status'] == 'UNKNOWN' for item in result['evidence']))
        self.assertEqual(inspected['contacts'], [])
        self.assertNotIn('secret detail', json.dumps(result))
        local.assert_not_called()

    def test_client_rejects_invalid_response(self):
        env = {'RESEARCH_SERVICE_URL': self.base, 'RESEARCH_SERVICE_TOKEN': 'test-token'}
        with patch.dict(os.environ, env), patch('app.research.server.audit_website_local', return_value={'unexpected': True}):
            with self.assertRaises(ResearchUnavailable):
                request_research('audit', 'https://clinic.test')

    def test_client_rejects_invalid_service_address(self):
        for address in ('http://[', 'http://localhost:invalid', 'file:///tmp/service'):
            with self.subTest(address=address), patch('app.research.client.service_url', return_value=address):
                with self.assertRaises(ResearchUnavailable):
                    request_research('audit', 'https://clinic.test')


class ResearchNetworkTests(unittest.TestCase):
    def test_private_and_special_addresses(self):
        for address in ['127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', '192.168.1.1']:
            with self.subTest(address=address), patch('app.research.network.socket.getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 443))]):
                with self.assertRaises(ValueError):
                    validate_public_url('https://clinic.test')

    def test_public_and_mixed_dns(self):
        public = (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))
        private = (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.1', 443))
        with patch('app.research.network.socket.getaddrinfo', return_value=[public]):
            validate_public_url('https://clinic.test')
        with patch('app.research.network.socket.getaddrinfo', return_value=[public, private]):
            with self.assertRaises(ValueError):
                validate_public_url('https://clinic.test')

    def test_redirect_checked_before_following(self):
        with patch('app.research.network.validate_public_url', side_effect=ValueError('private')) as validate:
            with self.assertRaises(ValueError):
                PublicRedirectHandler().redirect_request(None, None, 302, '', {}, 'http://127.0.0.1/')
        validate.assert_called_once_with('http://127.0.0.1/')
