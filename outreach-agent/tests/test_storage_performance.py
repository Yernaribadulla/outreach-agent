import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from app.storage.db import Database


class StoragePerformanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / 'test.db')

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_aggregates_without_join_multiplication(self):
        clinic = self.db.add_clinic({'name': 'Clinic', 'city': 'Test'})
        self.db.add_contact(clinic, 'info@clinic.test', 'https://clinic.test')
        self.db.add_contact(clinic, 'hello@clinic.test', 'https://clinic.test')
        for _ in range(3):
            self.db.add_analysis(clinic, 'fixture', {}, {'confidence': 1})
        result = self.db.list_clinics()[0]
        self.assertEqual(result['contact_count'], 2)
        self.assertEqual(result['analysis_count'], 3)
        self.assertEqual(len(result['emails'].split(',')), 2)
        self.assertNotIn('latest_draft_id', result)

    def test_no_latest_draft_query_per_clinic(self):
        def query_count(expected_clinics):
            queries = []
            self.db.conn.set_trace_callback(queries.append)
            try:
                self.assertEqual(len(self.db.list_clinics()), expected_clinics)
            finally:
                self.db.conn.set_trace_callback(None)
            return sum(query.lstrip().upper().startswith('SELECT') for query in queries)

        self.db.add_clinic({'name': 'Clinic 0', 'city': 'Test'})
        one_clinic = query_count(1)
        for i in range(1, 25):
            self.db.add_clinic({'name': f'Clinic {i}', 'city': 'Test'})
        self.assertEqual(query_count(25), one_clinic, 'Query count must not grow with the number of clinics.')

    def test_concurrent_writes(self):
        def add(i):
            clinic = self.db.add_clinic({'name': f'Concurrent {i}', 'city': 'Test'})
            self.db.add_analysis(clinic, 'fixture', {}, {'confidence': 1})
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(add, range(32)))
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM analyses').fetchone()[0], 32)
        self.assertEqual(len(self.db.list_clinics()), 32)
