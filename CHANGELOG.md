# Changelog

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.0.0/),
версионирование — [SemVer](https://semver.org/lang/ru/).

## [0.1.0] — 2026-07-04

### Added
- Офлайн-калькуляторы РФ (без ключа): `calc_vat`, `calc_usn`, `calc_insurance_ip`,
  `calc_ndfl` (прогрессивная шкала 2025+), `calc_patent`, `calc_penalty` — с формулой
  и ссылкой на статью НК РФ, расчёт через `Decimal`.
- `get_rates` — офлайн-снапшот ставок/лимитов (фиксвзносы ИП 2024–2027, шкала НДФЛ,
  лимиты УСН) + опция `fresh` для свежих данных с hosted (тариф Pro).
- Live-проверки через hosted-API (тариф Pro): `check_selfemployed`, `check_ip_status`,
  `check_disqualified`, `check_account_block`, `check_tax_arrears`.
- CLI argparse: `--help`, `--version`, `--transport`, `--host`, `--port`, `--log-level`.
- Дисклеймер и ссылка на первоисточник в каждом ответе.
