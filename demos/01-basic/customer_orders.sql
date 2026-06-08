-- dbt model: customer_orders
-- Joins staged orders to staged customers and aggregates revenue.
{{ config(materialized='table') }}

select
    o.customer_id                       as customer_id,
    concat(c.first_name, ' ', c.last_name) as full_name,
    c.email                             as email,
    sum(o.amount)                       as revenue,
    count(o.order_id)                   as order_count,
    max(o.created_at)                   as last_order_at
from {{ ref('stg_orders') }} as o
left join {{ ref('stg_customers') }} as c
    on o.customer_id = c.customer_id
where o.status = 'complete'
group by o.customer_id, c.first_name, c.last_name, c.email
