"""Accepted-drift ledger: suppression by (endpoint, code, field)
identity, the accept command round-trip, and strict validation."""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lcdiff import cli  # noqa: E402
from lcdiff import ledger as ledger_mod  # noqa: E402

FX = os.path.join(os.path.dirname(__file__), "fixtures")


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


def breaking_baseline(tmp):
    """A baseline whose diff against fault_required_nested's spec is a
    breaking REQUIRED_MISSING (6 samples, required 'address.zip' and
    'sku'-style fields never sent — same construction as the CLI
    exit-code test in test_lcdiff.py)."""
    ents = [{"request": {"method": "GET", "url": "https://x/users/%d" % i},
             "response": {"status": 200,
                          "content": {"mimeType": "application/json",
                                      "text": json.dumps(
                                          {"id": i, "name": "n",
                                           "address": {"city": "c"}})}}}
            for i in range(6)]
    harpath = os.path.join(tmp, "six.har")
    with open(harpath, "w") as fh:
        json.dump({"log": {"entries": ents}}, fh)
    bpath = os.path.join(tmp, "b.json")
    code, _, _ = run_cli(["observe", "--har", harpath, "--out", bpath])
    assert code == 0
    return bpath


SPEC = os.path.join(FX, "fault_required_nested.openapi.json")


class LedgerSuppression(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bpath = breaking_baseline(self.tmp)

    def diff(self, *extra, fmt="text"):
        return run_cli(["diff", "--baseline", self.bpath, "--spec", SPEC,
                        "--format", fmt, *extra])

    def write_ledger(self, entries):
        path = os.path.join(self.tmp, "ledger.json")
        with open(path, "w") as fh:
            json.dump({"version": 1, "accepted": entries}, fh)
        return path

    def test_breaking_without_ledger_exits_1(self):
        code, out, _ = self.diff()
        self.assertEqual(code, 1)
        self.assertIn("REQUIRED_MISSING", out)

    def test_accepted_breaking_finding_exits_0_and_is_reported(self):
        code, out, _ = self.diff(fmt="json")
        report = json.loads(out)
        f0 = report["findings"][0]
        ledger = self.write_ledger([{
            "endpoint": f0["endpoint"], "code": f0["code"],
            "field": f0["field"], "reason": "zip is optional in practice",
            "accepted_at": "2026-11-14"}])
        code, out, _ = self.diff("--ledger", ledger, fmt="json")
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["summary"]["accepted"], 1)
        self.assertEqual(report["accepted"][0]["accepted_reason"],
                         "zip is optional in practice")
        code, out, _ = self.diff("--ledger", ledger)
        self.assertEqual(code, 0)
        self.assertIn("[ACCEPTED] REQUIRED_MISSING", out)
        self.assertIn("zip is optional in practice", out)
        self.assertIn("No drift found.", out)

    def test_removing_acceptance_makes_finding_reappear(self):
        code, out, _ = self.diff(fmt="json")
        f0 = json.loads(out)["findings"][0]
        ledger = self.write_ledger([{
            "endpoint": f0["endpoint"], "code": f0["code"],
            "field": f0["field"], "reason": "tolerated"}])
        code, _, _ = self.diff("--ledger", ledger)
        self.assertEqual(code, 0)
        self.write_ledger([])  # acceptance removed
        code, out, _ = self.diff("--ledger", ledger)
        self.assertEqual(code, 1)
        self.assertIn("REQUIRED_MISSING", out)

    def test_identity_ignores_evidence_counts(self):
        f = {"code": "REQUIRED_MISSING", "severity": "breaking",
             "endpoint": "GET /users/{id}", "field": "address.zip",
             "evidence": {"present_count": 3}}
        ledger = {"version": 1, "accepted": [{
            "endpoint": "GET /users/{id}", "code": "REQUIRED_MISSING",
            "field": "address.zip", "reason": "known"}]}
        changed = dict(f, evidence={"present_count": 99})
        active, accepted = ledger_mod.apply_ledger([changed], ledger)
        self.assertEqual(active, [])
        self.assertEqual(len(accepted), 1)
        # a different field or code with the same endpoint stays active
        other = dict(f, field="address.city")
        active, _ = ledger_mod.apply_ledger([other], ledger)
        self.assertEqual(len(active), 1)


class AcceptCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bpath = breaking_baseline(self.tmp)

    def test_roundtrip(self):
        rpath = os.path.join(self.tmp, "report.json")
        code, _, _ = run_cli(["diff", "--baseline", self.bpath,
                              "--spec", SPEC, "--format", "json",
                              "--out", rpath])
        self.assertEqual(code, 1)
        lpath = os.path.join(self.tmp, "ledger.json")
        code, _, err = run_cli(["accept", "--report", rpath,
                                "--reason", "reviewed 2026-11-14",
                                "--out", lpath])
        self.assertEqual(code, 0, err)
        ledger = json.load(open(lpath))
        self.assertEqual(ledger["version"], 1)
        self.assertTrue(ledger["accepted"])
        self.assertTrue(all(e["reason"] == "reviewed 2026-11-14"
                            for e in ledger["accepted"]))
        code, out, _ = run_cli(["diff", "--baseline", self.bpath,
                                "--spec", SPEC, "--ledger", lpath])
        self.assertEqual(code, 0)
        self.assertIn("[ACCEPTED]", out)
        # accepting again adds nothing (identities already recorded)
        code, _, err = run_cli(["accept", "--report", rpath,
                                "--reason", "again", "--ledger", lpath,
                                "--out", lpath])
        self.assertEqual(code, 0, err)
        self.assertIn("accepted 0 new", err)

    def test_empty_reason_is_an_error(self):
        rpath = os.path.join(self.tmp, "report.json")
        run_cli(["diff", "--baseline", self.bpath, "--spec", SPEC,
                 "--format", "json", "--out", rpath])
        code, _, err = run_cli(["accept", "--report", rpath,
                                "--reason", "   ",
                                "--out", os.path.join(self.tmp, "l.json")])
        self.assertEqual(code, 2)
        self.assertIn("--reason", err)


class LedgerValidation(unittest.TestCase):
    def run_with_ledger(self, tmp, ledger_obj):
        bpath = breaking_baseline(tmp)
        lpath = os.path.join(tmp, "ledger.json")
        with open(lpath, "w") as fh:
            if isinstance(ledger_obj, str):
                fh.write(ledger_obj)
            else:
                json.dump(ledger_obj, fh)
        return run_cli(["diff", "--baseline", bpath, "--spec", SPEC,
                        "--ledger", lpath])

    def test_bad_ledgers_exit_2(self):
        tmp = tempfile.mkdtemp()
        entry = {"endpoint": "GET /users/{id}", "code": "REQUIRED_MISSING",
                 "field": "address.zip", "reason": "ok"}
        for bad in (
            {"version": 2, "accepted": []},                      # version
            {"version": 1, "accepted": [dict(entry, reason="")]},  # no reason
            {"version": 1, "accepted": [entry, entry]},          # duplicate
            {"version": 1, "accepted": [dict(entry, bogus=1)]},  # unknown key
            {"version": 1, "accepted": "nope"},                  # not a list
            "{not json",                                         # not JSON
        ):
            code, _, err = self.run_with_ledger(tmp, bad)
            self.assertEqual(code, 2, (bad, err))
            self.assertIn("error:", err)

    def test_missing_ledger_file_exit_2(self):
        tmp = tempfile.mkdtemp()
        bpath = breaking_baseline(tmp)
        code, _, err = run_cli(["diff", "--baseline", bpath, "--spec",
                                SPEC, "--ledger",
                                os.path.join(tmp, "nope.json")])
        self.assertEqual(code, 2)
        self.assertIn("not found", err)


if __name__ == "__main__":
    unittest.main()
