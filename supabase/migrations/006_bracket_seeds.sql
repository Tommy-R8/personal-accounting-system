-- Full current ARCA scale (A–K), confirmed 2026-07-13 against the official
-- ARCA categories page. D–G were already seeded in 005 from the legacy
-- Overview sheet; these are the remaining seven, same valid_from convention.
insert into monotributo_brackets (category, valid_from, annual_cap) values
  ('A', '2026-01-01', 10277988.13),
  ('B', '2026-01-01', 15058447.71),
  ('C', '2026-01-01', 21113696.52),
  ('H', '2026-01-01', 70113407.33),
  ('I', '2026-01-01', 78479211.62),
  ('J', '2026-01-01', 89872640.30),
  ('K', '2026-01-01', 108357084.05);
