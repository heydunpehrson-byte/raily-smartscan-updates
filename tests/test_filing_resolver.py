import unittest
from raily.filing import filing_components


class FilingResolverTests(unittest.TestCase):
    def test_missing_and_legacy_placeholders(self):
        for railroad, location in [(None, None), ('', ''), ('  ', '\t'), ('Unknown Railroad', 'Unknown Location')]:
            self.assertEqual(filing_components(railroad, location), ('Unassigned Railroad', 'General'))
        self.assertEqual(filing_components('IRAIL', ''), ('IRAIL', 'General'))

    def test_unsafe_components_rejected(self):
        for value in ['../escape', '..', 'C:\\escape', 'CON', 'bad:name']:
            with self.assertRaises(ValueError): filing_components(value, '')
