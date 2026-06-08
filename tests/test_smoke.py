"""Smoke tests for LINEAGEMAP. No network. Standard library only."""
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lineagemap import extract_lineage, build_dbt_graph, TOOL_NAME, TOOL_VERSION
from lineagemap.cli import main


class TestCore(unittest.TestCase):
    def test_simple_qualified(self):
        lin = extract_lineage("select a.id, a.name from users a", "m")
        self.assertEqual(lin.upstream_tables, ["users"])
        cols = {c.output: c.sources for c in lin.columns}
        self.assertEqual(cols["id"], ["users.id"])
        self.assertEqual(cols["name"], ["users.name"])

    def test_join_alias_resolution(self):
        sql = (
            "select o.customer_id as cid, c.email as email "
            "from orders o left join customers c on o.customer_id = c.id"
        )
        lin = extract_lineage(sql, "m")
        self.assertIn("orders", lin.upstream_tables)
        self.assertIn("customers", lin.upstream_tables)
        cols = {c.output: c.sources for c in lin.columns}
        self.assertEqual(cols["cid"], ["orders.customer_id"])
        self.assertEqual(cols["email"], ["customers.email"])

    def test_expression_multiple_sources(self):
        sql = ("select concat(c.first_name, c.last_name) as full_name "
               "from customers c")
        lin = extract_lineage(sql, "m")
        srcs = lin.columns[0].sources
        self.assertIn("customers.first_name", srcs)
        self.assertIn("customers.last_name", srcs)
        self.assertEqual(lin.columns[0].output, "full_name")

    def test_star(self):
        lin = extract_lineage("select * from events", "m")
        self.assertTrue(lin.columns[0].is_star)
        self.assertEqual(lin.columns[0].sources, ["events.*"])

    def test_dbt_ref_and_source(self):
        sql = ("select x.id from {{ ref('stg_users') }} x "
               "join {{ source('raw','events') }} e on x.id = e.uid")
        lin = extract_lineage(sql, "m")
        self.assertIn("stg_users", lin.upstream_tables)
        self.assertIn("raw__events", lin.upstream_tables)

    def test_dbt_graph(self):
        a = extract_lineage("select id from {{ ref('b') }} b", "a")
        b = extract_lineage("select id from raw_table", "b")
        graph = build_dbt_graph([a, b])
        self.assertEqual(graph["a"], ["b"])
        self.assertEqual(graph["b"], [])

    def test_aggregate_no_func_in_sources(self):
        lin = extract_lineage("select sum(o.amount) as revenue from orders o", "m")
        self.assertEqual(lin.columns[0].sources, ["orders.amount"])


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.demo = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "demos", "01-basic", "customer_orders.sql",
        )

    def _capture(self, argv, stdin=None):
        out, err = io.StringIO(), io.StringIO()
        old = (sys.stdout, sys.stderr, sys.stdin)
        sys.stdout, sys.stderr = out, err
        if stdin is not None:
            sys.stdin = io.StringIO(stdin)
        try:
            code = main(argv)
        finally:
            sys.stdout, sys.stderr, sys.stdin = old
        return code, out.getvalue(), err.getvalue()

    def test_json_demo(self):
        code, out, _ = self._capture(["--format", "json", "trace", self.demo])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["tool"], TOOL_NAME)
        self.assertEqual(data["version"], TOOL_VERSION)
        model = data["models"][0]
        cols = {c["output"]: c["sources"] for c in model["columns"]}
        self.assertEqual(cols["customer_id"], ["stg_orders.customer_id"])
        self.assertIn("stg_customers.email", cols["email"])
        self.assertEqual(cols["revenue"], ["stg_orders.amount"])

    def test_stdin(self):
        code, out, _ = self._capture(
            ["--format", "json", "trace"],
            stdin="select u.id as uid from users u",
        )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["models"][0]["columns"][0]["output"], "uid")

    def test_empty_stdin_fails(self):
        code, _, err = self._capture(["trace"], stdin="   ")
        self.assertEqual(code, 1)
        self.assertIn("error", err)

    def test_missing_file_fails(self):
        code, _, err = self._capture(["trace", "does_not_exist.sql"])
        self.assertEqual(code, 1)
        self.assertIn("error", err)

    def test_no_command_returns_2(self):
        code, _, _ = self._capture([])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
