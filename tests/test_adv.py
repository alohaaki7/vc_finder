import io
import unittest
from datetime import date
from collect_adv_signals import parse_adv_xml, build_signal_rows, classify_adv_signal
from server import app

class AdvTests(unittest.TestCase):
    def test_old_vc_is_retained_and_not_called_recent(self):
        firms, stamp = parse_adv_xml(io.BytesIO(b'<IAPDFirmSECReport GenOn="2026-09-07"><Firms><Firm><Info FirmCrdNb="1" BusNm="Example"/><Rgstn FirmType="ERA" St="ACTIVE" Dt="2010-01-01"/><Filing Dt="2026-09-07"/><FormInfo><Part1A><Item2B Q2B1="Y"/></Part1A></FormInfo></Firm></Firms></IAPDFirmSECReport>'))
        rows = build_signal_rows(firms, feed_date=stamp, source_url='test', as_of=date(2026,9,7))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['freshness_bucket'], 'older_registration')
        self.assertEqual(rows[0]['verification_status'], 'unresolved')

    def test_private_fund_is_retained_as_unknown_strategy(self):
        self.assertEqual(classify_adv_signal({'firm_type':'ERA','business_name':'Example'})[0], 'strategy_unresolved')
        self.assertEqual(classify_adv_signal({'firm_type':'Registered','business_name':'Wealth planning'}), (None,None))

    def test_real_api_and_page(self):
        client=app.test_client()
        payload=client.get('/api/adv').get_json()
        self.assertGreater(payload['metadata']['total_firms_scanned'], 0)
        self.assertTrue(any(r['review'].get('established') for r in payload['rows']))
        self.assertEqual(client.get('/adv').status_code, 200)
