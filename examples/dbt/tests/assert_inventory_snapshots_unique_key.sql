-- Composite primary key of dbo.inventory_snapshots must stay unique.
SELECT snapshot_month, site_id, product_id, COUNT(*) AS n
FROM {{ ref('inventory_snapshots') }}
GROUP BY snapshot_month, site_id, product_id
HAVING COUNT(*) > 1
