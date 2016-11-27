# -*- coding: UTF-8 -*-
# NOTE: Launch all tests with `nosetests` command from git repo root dir.

import unittest
import json
import os

from StringIO import StringIO
from tempfile import NamedTemporaryFile
from textwrap import dedent

from mock import patch
from nose.plugins.attrib import attr
import psycopg2

import main

env = json.load(open('.env.test.json'))


connection = psycopg2.connect(
    database=env['REDSHIFT_DBNAME'],
    user=env['REDSHIFT_USER'],
    password=env['REDSHIFT_PASSWORD'],
    host=env['REDSHIFT_HOST'],
    port=env['REDSHIFT_PORT']
)


class AggregationTestCase(unittest.TestCase):
    # Test aggregation functions by week, ip, place and risk.

    def setUp(self):
        # Patch main connection with the test one.
        patch('main.connection', connection).start()

        # Set isolation level to run CREATE DATABASE statement outside of transactions.
        main.connection.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)

        self.cursor = main.connection.cursor()

        # Recreate logentry table
        try:
            self.cursor.execute("DROP TABLE %s" % (main.tablename))
        except:
            pass
        main.create_table()

    def test_group_by_week(self):
        # GIVEN 3 entries of the same asn, risk and country, two of which within one week
        ntp_scan_csv = dedent('''\
        ts,ip,risk_id,asn,cc
        2016-09-20T00:00:01+00:00,71.3.0.1,2,12252,US
        2016-09-28T00:00:01+00:00,190.81.134.82,2,12252,US
        2016-09-29T00:00:01+00:00,190.81.135.11,2,12252,US
        ''')
        self.cursor.copy_expert("COPY logentry from STDIN csv header", StringIO(ntp_scan_csv))

        # WHEN grouped entries get created
        main.create_count()

        # THEN count table should have 2 entries which get grouped, and one entry which stands alone
        self.cursor.execute('select * from count;')
        self.assertEqual(
            self.cursor.fetchall(),
            [
                (2, 'US', 12252L, '2016-09-19', 'monthly', 1),
                (2, 'US', 12252L, '2016-09-26', 'monthly', 2)  # grouped two entries
            ])
    
    def test_group_by_distinct_ip(self):
        # GIVEN 7 entries of the same asn, risk and country from hostA (71.3.0.1) and hostB (190.81.134)
        # First week: 2 hostA entries, 1 hostB entry
        # Second week: 2 hostA entries, 2 hostB entries
        ntp_scan_csv = dedent('''\
        ts,ip,risk_id,asn,cc
        2016-09-20T00:00:01+00:00,71.3.0.1,2,12252,US
        2016-09-20T00:00:01+00:00,71.3.0.1,2,12252,US
        2016-09-20T00:00:01+00:00,190.81.134.11,2,12252,US
        2016-09-27T00:00:01+00:00,71.3.0.1,2,12252,US
        2016-09-28T00:00:01+00:00,71.3.0.1,2,12252,US
        2016-09-28T00:00:01+00:00,190.81.134.11,2,12252,US
        2016-09-29T00:00:01+00:00,190.81.134.11,2,12252,US
        ''')
        self.cursor.copy_expert("COPY logentry from STDIN csv header", StringIO(ntp_scan_csv))

        # WHEN grouped entries get created
        main.create_count()

        # THEN count table should have 2 rows corresponding to weeks, with properly grouped entries
        self.cursor.execute('select * from count;')
        self.assertEqual(
            self.cursor.fetchall(),
            [
                # First week: 2 entries from hostA count as one
                (2, 'US', 12252L, '2016-09-19', 'monthly', 2),

                # Second week: duplicated entries for hostA and hostB will merge to single one for each host
                (2, 'US', 12252L, '2016-09-26', 'monthly', 2)
            ])

    def test_group_by_ip_week_distinct_risk(self):
        # GIVEN 4 entries of the same asn, week and country from hostA (71.3.0.1) and hostB (190.81.134)
        # hostA: 2 entries of the same risk type
        # hostB: 2 entries of different risk type
        ntp_scan_csv = dedent('''\
        ts,ip,risk_id,asn,cc
        2016-09-28T00:00:01+00:00,71.3.0.1,2,12252,US
        2016-09-29T00:00:01+00:00,71.3.0.1,2,12252,US
        2016-09-28T00:00:01+00:00,190.81.134.11,2,12252,US
        2016-09-29T00:00:01+00:00,190.81.134.11,99,12252,US
        ''')
        self.cursor.copy_expert("COPY logentry from STDIN csv header", StringIO(ntp_scan_csv))

        # WHEN grouped entries get created
        main.create_count()

        # THEN count table should have 2 rows corresponding to different risks, with properly grouped entries
        self.cursor.execute('select * from count;')
        self.assertEqual(
            self.cursor.fetchall(),
            [

                # Risk type 99 - one entry from hostB
                (99, 'US', 12252L, '2016-09-26', 'monthly', 1),

                # Risk type 2 - 2 total count: one entry from hostB, and two from hostA, which grouped into one
                (2, 'US', 12252L, '2016-09-26', 'monthly', 2)
            ])

    def test_group_by_country(self):
        # GIVEN 3 entries of the same risk and week, two of which are from one country, but different asn
        ntp_scan_csv = dedent('''\
        ts,ip,risk_id,asn,cc
        2016-09-28T00:00:01+00:00,190.81.134.82,2,4444,US
        2016-09-29T00:00:01+00:00,190.81.134.11,2,12252,US
        2016-09-29T00:00:01+00:00,190.81.134.11,2,3333,DE
        ''')
        self.cursor.copy_expert("COPY logentry from STDIN csv header", StringIO(ntp_scan_csv))

        # WHEN grouped entries get created
        main.create_count()
        main.create_count_by_country()

        # THEN count_by_country table should have 2 entries which get grouped, and one entry which stands alone
        self.cursor.execute('select * from count_by_country;')
        self.assertEqual(
            self.cursor.fetchall(),
            [
                (2, 'DE', '2016-09-26', 1L, 0.0, 0),
                (2, 'US', '2016-09-26', 2L, 0.0, 0)   # 2 entries grouped by country
            ])

    def test_group_by_risk(self):
        # GIVEN 3 entries, of the same week, two of which have same risk type, but different countries
        ntp_scan_csv = dedent('''\
        ts,ip,risk_id,asn,cc
        2016-09-28T00:00:01+00:00,190.81.134.82,7,4444,US
        2016-09-29T00:00:01+00:00,190.81.134.11,2,12252,US
        2016-09-29T00:00:01+00:00,190.81.134.11,2,3333,DE
        ''')
        self.cursor.copy_expert("COPY logentry from STDIN csv header", StringIO(ntp_scan_csv))

        # WHEN grouped entries get created
        main.create_count()
        main.create_count_by_country()
        main.create_count_by_risk()

        # THEN count_by_risk table should have 2 entries which get grouped, and one entry which stands alone
        self.cursor.execute('select * from count_by_risk;')
        self.assertEqual(
            self.cursor.fetchall(),
            [
                (7, '2016-09-26', 1L, 1L),
                (2, '2016-09-26', 2L, 1L)   # 2 enreies grouped by risk
            ])

class ScoresTestCase(unittest.TestCase):
    # Test scores calculation.

    def setUp(self):
        # Patch main connection with the test one.
        patch('main.connection', connection).start()

        # Set isolation level to run CREATE DATABASE statement outside of transactions.
        main.connection.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)

        self.cursor = main.connection.cursor()

        # Recreate logentry table
        try:
            self.cursor.execute("DROP TABLE %s" % (main.tablename))
        except:
            pass
        main.create_table()

    def test_scores5(self):
        # GIVEN 5 entries from different hosts, of the same risk and week, 2 from US, and 3 from DE
        ntp_scan_csv = dedent('''\
        ts,ip,risk_id,asn,cc
        2016-09-28T00:00:01+00:00,71.3.0.1,2,4444,US
        2016-09-29T00:00:01+00:00,71.3.0.9,2,12252,US
        2016-09-29T00:00:01+00:00,190.81.134.1,2,3333,DE
        2016-09-29T00:00:01+00:00,190.81.134.2,2,3333,DE
        2016-09-29T00:00:01+00:00,190.81.134.3,2,3333,DE
        ''')
        self.cursor.copy_expert("COPY logentry from STDIN csv header", StringIO(ntp_scan_csv))

        # WHEN grouped entries get created
        main.create_count()
        main.create_count_by_country()
        main.create_count_by_risk()
        # AND score calcualted
        main.update_with_scores()

        # THEN count_by_country table should have proper scores
        self.cursor.execute('select * from count_by_country;')
        self.assertEqual(
            self.cursor.fetchall(),
            [
                (2, 'DE', '2016-09-26', 3L, 100.0, 0),   # 100% score
                (2, 'US', '2016-09-26', 2L, 63.093, 0)   # 63% score
            ])

    def test_scores6(self):
        # GIVEN 6 entries from different hosts, of the same risk and week, 2 from US, and 4 from DE
        ntp_scan_csv = dedent('''\
        ts,ip,risk_id,asn,cc
        2016-09-28T00:00:01+00:00,71.3.0.1,2,4444,US
        2016-09-29T00:00:01+00:00,71.3.0.9,2,12252,US
        2016-09-29T00:00:01+00:00,190.81.134.1,2,3333,DE
        2016-09-29T00:00:01+00:00,190.81.134.2,2,3333,DE
        2016-09-29T00:00:01+00:00,190.81.134.3,2,3333,DE
        2016-09-29T00:00:01+00:00,190.81.134.4,2,3333,DE
        ''')
        self.cursor.copy_expert("COPY logentry from STDIN csv header", StringIO(ntp_scan_csv))

        # WHEN grouped entries get created
        main.create_count()
        main.create_count_by_country()
        main.create_count_by_risk()
        # AND score calcualted
        main.update_with_scores()

        # THEN count_by_country table should have proper scores
        self.cursor.execute('select * from count_by_country;')
        self.assertEqual(
            self.cursor.fetchall(),
            [
                (2, 'DE', '2016-09-26', 4L, 100.0, 0),   # 100% score
                (2, 'US', '2016-09-26', 2L, 50.0, 0)   # 50% score
            ])

    def test_scores7(self):
        # GIVEN 7 entries from different hosts, of the same risk and week, 2 from US, and 5 from DE
        ntp_scan_csv = dedent('''\
        ts,ip,risk_id,asn,cc
        2016-09-28T00:00:01+00:00,71.3.0.1,2,4444,US
        2016-09-29T00:00:01+00:00,71.3.0.9,2,12252,US
        2016-09-29T00:00:01+00:00,190.81.134.1,2,3333,DE
        2016-09-29T00:00:01+00:00,190.81.134.2,2,3333,DE
        2016-09-29T00:00:01+00:00,190.81.134.3,2,3333,DE
        2016-09-29T00:00:01+00:00,190.81.134.4,2,3333,DE
        2016-09-29T00:00:01+00:00,190.81.134.5,2,3333,DE
        ''')
        self.cursor.copy_expert("COPY logentry from STDIN csv header", StringIO(ntp_scan_csv))

        # WHEN grouped entries get created
        main.create_count()
        main.create_count_by_country()
        main.create_count_by_risk()
        # AND score calcualted
        main.update_with_scores()

        # THEN count_by_country table should have proper scores
        self.cursor.execute('select * from count_by_country;')
        self.assertEqual(
            self.cursor.fetchall(),
            [
                (2, 'DE', '2016-09-26', 5L, 100.0, 0),   # 100% score
                (2, 'US', '2016-09-26', 2L, 43.0677, 0)   # 43% score
            ])

class MetadataTestCase(unittest.TestCase):
    # Test scores calculation.

    def test_create_manifest(self):
        datapackage = dedent('''{"resources":[
        {"path": ["ntp-scan/ntp-scan.20000101.csv.gz"],
        "schema": {"fields": []}, "name": "openntp", "compression": "gz", "format": "csv"},
        {"path": ["ssdp-data/ssdp-data.20000101.csv.gz"],
        "schema": {"fields": []}, "name": "openssdp", "compression": "gz", "format": "csv"},
        {"path": [],
        "schema": {"fields": []}, "name": "spam", "compression": "gz", "format": "csv"},
        {"path": ["snmp-data/snmp-data.20000101.csv.gz"],
        "schema": {"fields": []}, "name": "opensnmp", "compression": "gz", "format": "csv"},
        {"path": ["dns-scan/dns-scan.20000101.csv.gz"],
        "schema": {"fields": []}, "name": "opendns", "compression": "gz", "format": "csv"}],
        "name": "cybergreen_enriched_data",
        "title": "CyberGreen Enriched Data"}''')
        expected_manifest = {'entries': [
            {'url': u's3://test.bucket/test/key/ntp-scan/ntp-scan.20000101.csv.gz',
             'mandatory': True},
            {'url': u's3://test.bucket/test/key/ssdp-data/ssdp-data.20000101.csv.gz',
             'mandatory': True},
            {'url': u's3://test.bucket/test/key/snmp-data/snmp-data.20000101.csv.gz',
             'mandatory': True},
            {'url': u's3://test.bucket/test/key/dns-scan/dns-scan.20000101.csv.gz',
             'mandatory': True}
            ]}
        manifest = main.create_manifest(datapackage, 'test.bucket','test/key')
        self.assertEquals(manifest,expected_manifest)