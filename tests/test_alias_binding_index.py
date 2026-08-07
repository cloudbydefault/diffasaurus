import time
import unittest
from datetime import datetime

from diffasaurus.core.entity.bindings import AliasBindingIndex, ResolvedAlias


class AliasBindingIndexTests(unittest.TestCase):
    def test_no_observation_is_unbound(self):
        index = AliasBindingIndex()
        result = index.resolve("upn", "missing@example.com", datetime(2026, 8, 4, 4, 0, 0))
        self.assertEqual(result.status, "unbound")

    def test_one_historical_binding(self):
        index = AliasBindingIndex()
        index.record(
            "upn",
            "ada@example.com",
            datetime(2026, 8, 4, 4, 0, 0),
            "user-1",
            "Entra_Users_Properties",
        )
        result = index.resolve("upn", "ada@example.com", datetime(2026, 8, 4, 4, 30, 0))
        self.assertEqual(result, ResolvedAlias(status="bound", immutable_id="user-1"))

    def test_future_observation_excluded(self):
        index = AliasBindingIndex()
        index.record(
            "upn",
            "ada@example.com",
            datetime(2026, 8, 4, 6, 0, 0),
            "user-1",
            "Entra_Users_Properties",
        )
        result = index.resolve("upn", "ada@example.com", datetime(2026, 8, 4, 4, 0, 0))
        self.assertEqual(result.status, "unbound")

    def test_alias_changes_owner_over_time(self):
        index = AliasBindingIndex()
        index.record(
            "upn",
            "shared@example.com",
            datetime(2026, 8, 4, 4, 0, 0),
            "user-1",
            "Entra_Users_Properties",
        )
        index.record(
            "upn",
            "shared@example.com",
            datetime(2026, 8, 4, 6, 0, 0),
            "user-2",
            "Entra_Users_Properties",
        )
        early = index.resolve(
            "upn", "shared@example.com", datetime(2026, 8, 4, 5, 0, 0)
        )
        late = index.resolve(
            "upn", "shared@example.com", datetime(2026, 8, 4, 7, 0, 0)
        )
        self.assertEqual(early, ResolvedAlias(status="bound", immutable_id="user-1"))
        self.assertEqual(late, ResolvedAlias(status="bound", immutable_id="user-2"))

    def test_same_timestamp_ambiguity(self):
        index = AliasBindingIndex()
        index.record(
            "upn",
            "shared@example.com",
            datetime(2026, 8, 4, 4, 0, 0),
            "user-1",
            "Entra_Users_Properties",
        )
        index.record(
            "upn",
            "shared@example.com",
            datetime(2026, 8, 4, 4, 0, 0),
            "user-2",
            "Entra_Users_Properties",
        )
        result = index.resolve("upn", "shared@example.com", datetime(2026, 8, 4, 4, 5, 0))
        self.assertEqual(result.status, "ambiguous")
        self.assertEqual(result.candidates, frozenset({"user-1", "user-2"}))

    def test_unrelated_aliases_do_not_affect_lookup(self):
        index = AliasBindingIndex()
        index.record(
            "upn",
            "other@example.com",
            datetime(2026, 8, 4, 4, 0, 0),
            "user-9",
            "Entra_Users_Properties",
        )
        index.record(
            "upn",
            "ada@example.com",
            datetime(2026, 8, 4, 4, 0, 0),
            "user-1",
            "Entra_Users_Properties",
        )
        result = index.resolve("upn", "ada@example.com", datetime(2026, 8, 4, 4, 5, 0))
        self.assertEqual(result, ResolvedAlias(status="bound", immutable_id="user-1"))

    def test_values_for_immutable_id_historical_filtering(self):
        index = AliasBindingIndex()
        index.record(
            "upn",
            "old@example.com",
            datetime(2026, 8, 4, 4, 0, 0),
            "user-1",
            "Entra_Users_Properties",
        )
        index.record(
            "upn",
            "new@example.com",
            datetime(2026, 8, 4, 6, 0, 0),
            "user-1",
            "Entra_Users_Properties",
        )
        early = index.values_for_immutable_id("user-1", datetime(2026, 8, 4, 5, 0, 0))
        late = index.values_for_immutable_id("user-1", datetime(2026, 8, 4, 7, 0, 0))
        self.assertEqual(early, {"old@example.com"})
        self.assertEqual(late, {"old@example.com", "new@example.com"})

    def test_out_of_order_records_remain_correct(self):
        index = AliasBindingIndex()
        index.record(
            "upn",
            "ada@example.com",
            datetime(2026, 8, 4, 6, 0, 0),
            "user-1",
            "Entra_Users_Properties",
        )
        index.record(
            "upn",
            "ada@example.com",
            datetime(2026, 8, 4, 4, 0, 0),
            "user-1",
            "Entra_Users_Properties",
        )
        result = index.resolve("upn", "ada@example.com", datetime(2026, 8, 4, 5, 0, 0))
        self.assertEqual(result, ResolvedAlias(status="bound", immutable_id="user-1"))

    def test_large_collection_returns_correct_results(self):
        index = AliasBindingIndex()
        target_alias = "target@example.com"
        for offset in range(5000):
            index.record(
                "upn",
                f"noise{offset}@example.com",
                datetime(2026, 8, 4, 4, 0, offset % 60),
                f"user-{offset}",
                "Entra_Users_Properties",
            )
        index.record(
            "upn",
            target_alias,
            datetime(2026, 8, 4, 4, 0, 0),
            "target-user",
            "Entra_Users_Properties",
        )
        index.record(
            "upn",
            target_alias,
            datetime(2026, 8, 4, 6, 0, 0),
            "target-user-2",
            "Entra_Users_Properties",
        )
        early = index.resolve("upn", target_alias, datetime(2026, 8, 4, 5, 0, 0))
        late = index.resolve("upn", target_alias, datetime(2026, 8, 4, 7, 0, 0))
        self.assertEqual(early, ResolvedAlias(status="bound", immutable_id="target-user"))
        self.assertEqual(late, ResolvedAlias(status="bound", immutable_id="target-user-2"))

    def test_large_collection_microbenchmark_is_reasonable(self):
        index = AliasBindingIndex()
        for offset in range(10000):
            index.record(
                "upn",
                f"user{offset % 1000}@example.com",
                datetime(2026, 8, 4, 4, 0, offset % 59),
                f"immutable-{offset}",
                "Entra_Users_Properties",
            )
        start = time.perf_counter()
        for probe in range(2000):
            index.resolve(
                "upn",
                f"user{probe % 1000}@example.com",
                datetime(2026, 8, 4, 4, 0, 30),
            )
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 2.0)


if __name__ == "__main__":
    unittest.main()
