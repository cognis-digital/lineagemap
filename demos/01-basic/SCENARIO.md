# Demo 01 - Basic column lineage from a dbt model

This scenario shows LINEAGEMAP resolving **column-level lineage** through a
dbt model that joins two upstream refs with table aliases.

## Input

`customer_orders.sql` is a dbt model that:

- references `stg_orders` and `stg_customers` via `{{ ref(...) }}`
- joins them with aliases `o` and `c`
- builds derived columns (`full_name` via `concat`, `revenue` via `sum`)

## Run it

```bash
# Table view
python -m lineagemap --format table trace demos/01-basic/customer_orders.sql

# JSON (machine-readable, for piping into a catalog)
python -m lineagemap --format json trace demos/01-basic/customer_orders.sql

# From stdin
cat demos/01-basic/customer_orders.sql | python -m lineagemap trace
```

## What to expect

Each output column is mapped back to the real upstream `table.column` it
derives from. For example:

```
customer_id          <- stg_orders.customer_id
full_name            <- stg_customers.first_name, stg_customers.last_name
revenue              <- stg_orders.amount
```

The dbt `{{ ref('stg_orders') }}` calls are rewritten to plain model names so
the `upstream` list and (`--graph`) dependency edges line up with your dbt DAG.
