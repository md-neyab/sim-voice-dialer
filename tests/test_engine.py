#!/usr/bin/env python3
"""
Unit tests — python3 -m unittest discover -s tests -v
Koi pytest nahi, sirf standard library. Asterisk bhi nahi chahiye:
simulate mode aur temp folders se poora engine test hota hai.
"""

import csv
import os
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "engine"))

import dialer  # noqa: E402
import report  # noqa: E402


class TestNormalise(unittest.TestCase):
    def test_10_digit(self):
        self.assertEqual(dialer.normalise("9811012345"), "919811012345")

    def test_plus_91_with_spaces(self):
        self.assertEqual(dialer.normalise("+91 98110 12345"), "919811012345")

    def test_zero_prefix(self):
        self.assertEqual(dialer.normalise("09811012345"), "919811012345")

    def test_plain_91(self):
        self.assertEqual(dialer.normalise("919811012345"), "919811012345")

    def test_invalid(self):
        self.assertIsNone(dialer.normalise("12345"))
        self.assertIsNone(dialer.normalise("abcdefghij"))
        self.assertIsNone(dialer.normalise(""))
        self.assertIsNone(dialer.normalise("number"))  # CSV header line


class TestReadNumbers(unittest.TestCase):
    def test_dedupe_and_order(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "n.csv")
            with open(path, "w") as f:
                f.write("number\n9811012345\n+91 98110 12345\n9999999999\nbad\n9811012345\n")
            self.assertEqual(dialer.read_numbers(path),
                             ["919811012345", "919999999999"])


class TestCallFile(unittest.TestCase):
    def test_content(self):
        content = dialer.build_call_file("919811012345", 40)
        self.assertIn("Channel: Dongle/dongle0/919811012345", content)
        self.assertIn("Context: broadcast", content)
        self.assertIn("WaitTime: 40", content)

    def test_queue_call_atomic(self):
        with tempfile.TemporaryDirectory() as spool:
            final = dialer.queue_call("919811012345", 40, spool=spool)
            self.assertTrue(os.path.exists(final))
            # temp file spool me nahi bacha hona chahiye
            leftovers = [n for n in os.listdir(spool) if n.startswith(".tmp-")]
            self.assertEqual(leftovers, [])
            with open(final) as f:
                self.assertIn("Dongle/dongle0/919811012345", f.read())


class TestSimulateEndToEnd(unittest.TestCase):
    def test_simulate_writes_cdr_and_report_counts(self):
        with tempfile.TemporaryDirectory() as d:
            cdr = os.path.join(d, "Master.csv")
            numbers_csv = os.path.join(d, "numbers.csv")
            nums = [f"981100000{i}" for i in range(1, 7)]  # 6 fake numbers
            with open(numbers_csv, "w") as f:
                f.write("number\n" + "\n".join(nums) + "\n")

            old = os.environ.get("SVD_CDR_PATH")
            os.environ["SVD_CDR_PATH"] = cdr
            try:
                rc = dialer.main(["--csv", numbers_csv, "--simulate",
                                  "--seed", "7", "--gap", "5",
                                  "--log", os.path.join(d, "campaign.log")])
            finally:
                if old is None:
                    os.environ.pop("SVD_CDR_PATH", None)
                else:
                    os.environ["SVD_CDR_PATH"] = old
            self.assertEqual(rc, 0)

            rows = report.load_rows(cdr)
            self.assertEqual(len(rows), 6)
            allowed = {"ANSWERED", "NO ANSWER", "BUSY", "FAILED"}
            for r in rows:
                self.assertIn(r[report.DISP], allowed)
                self.assertIn("Dongle", r[report.CHANNEL])

            counts, latest = report.summarise(rows)
            self.assertEqual(sum(counts.values()), 6)
            self.assertEqual(len(latest), 6)
            answered_rows = [r for r in rows if r[report.DISP] == "ANSWERED"]
            self.assertEqual(counts.get("ANSWERED", 0), len(answered_rows))
            for r in answered_rows:
                self.assertGreater(int(r[report.BILLSEC]), 0)


class TestDryRun(unittest.TestCase):
    def test_dry_run_no_spool_needed(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "n.csv")
            with open(path, "w") as f:
                f.write("9811012345\n")
            rc = dialer.main(["--csv", path, "--dry-run",
                              "--log", os.path.join(d, "log.txt")])
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
