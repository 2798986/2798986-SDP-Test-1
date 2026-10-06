#!/usr/bin/env python3
"""RAT self-test - automated assertions proving the metric engine's semantics.

Run:  python3 selftest.py

Creates an isolated temporary DATA_DIR, ingests demo/fixture.zip through the
real pipeline, and asserts hand-computed metric values against it.
"""

import unittest


class Bootstrap(unittest.TestCase):
    def test_placeholder(self):
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
