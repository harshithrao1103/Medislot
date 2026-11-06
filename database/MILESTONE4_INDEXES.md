# Milestone 4 doctor discovery indexes

Add the following to existing dev databases through a reviewed schema migration (not a destructive reset):

```sql
CREATE INDEX idx_users_name ON users(name);
CREATE INDEX idx_doctors_hospital_city ON doctors(hospital_name,city);
CREATE INDEX idx_doctors_city_specialization ON doctors(city,specialization_id);
```

`doctors.specialization_id` is already indexed by the foreign key. `specializations.specialization_name` is UNIQUE. The name index supports sorting and prefix searches; the hospital/city and city/specialization indexes are useful for equality/prefix patterns. Current case-insensitive substring (`LIKE '%term%'`) filters support arbitrary combinations but **cannot fully exploit B-tree indexes**. For a larger dataset consider FULLTEXT indexes, a normalized prefix-search contract, or an external search engine. Run `EXPLAIN` for each actual query shape before claiming index use. No unverified performance claims.
