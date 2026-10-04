# OSWDocs — Система электронного документооборота (СЭД)

## Введение

OSWDocs — система электронного документооборота для автоматизации процессов обработки заявлений и документов с ролевой моделью доступа, интеграциями с Active Directory (LDAPS), 1С ЗУП (OData) и корпоративной почтой. Проект развёртывается в среде Docker/Compose под Linux.

## Назначение

Назначение системы — организация электронного документооборота, маршрутизация заявок по процессам, контроль сроков согласования, хранение вложений, ведение аудита действий пользователей.

## Терминология

- **СЭД** — система электронного документооборота.
- **Заявка (Request)** — единица документооборота с определённым маршрутом согласования.
- **Маршрут (Route)** — последовательность шагов согласования.
- **Шаг согласования (Approval Step)** — этап маршрута с назначением ответственных.
- **Аудит (Audit Log)** — журнал событий системы.
- **Ролевая модель** — разграничение прав доступа по ролям.



[![License: Proprietary](https://img.shields.io/badge/License-Proprietary-red.svg)](#лицензирование)
[![Platform: Linux/Docker](https://img.shields.io/badge/Platform-Linux%20%7C%20Docker-blue.svg)](https://www.docker.com/)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-green.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-Latest-009688.svg)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-18.x-61DAFB.svg)](https://react.dev/)
[![PostgreSQL 17](https://img.shields.io/badge/PostgreSQL-17-336791.svg)](https://www.postgresql.org/)
[![NGINX](https://img.shields.io/badge/NGINX-stable-alpine-009639.svg)](https://nginx.org/)
[![Redis 7](https://img.shields.io/badge/Redis-7-DC382D.svg)](https://redis.io/)



## Лицензирование

Проект предназначен для внутреннего использования. Лицензия — проприетарная (Proprietary).

## Технологический стек

- **Backend**: FastAPI 0.115+, SQLAlchemy 2.0+, Alembic, python-docx-template, python-multipart, uvicorn[standard], httpx
- **Frontend**: React 18, TypeScript, Vite
- **БД/Кэш**: PostgreSQL 17, Redis 7
- **Интеграции**: ldap3 (LDAPS), PyJWT, pydantic-settings
- **Инфраструктура**: Docker, Docker Compose, NGINX, systemd, LibreOffice

## Структура проекта

См. [STRUCTURE.md](STRUCTURE.md).

## Конфигурация и запуск

1. Скопируйте .env.example в .env и заполните параметры.
2. docker compose config.
3. docker compose up --build -d.

Секреты только в .env, файл не коммитится.

## Документация

- [AGENTS.md](AGENTS.md) — правила для ИИ-агентов
- [STRUCTURE.md](STRUCTURE.md) — структура проекта
- [TEMPLATES.md](TEMPLATES.md) — шаблоны
- [PLAN.md](PLAN.md) — план разработки
- [AI_SKILLS_MCP.md](AI_SKILLS_MCP.md) — навыки и MCP

## Безопасность

- AD_WRITE_ENABLED=false по умолчанию
- Ролевая модель доступа
- Аудит udit_log (append-only)
- TLS 1.2+, HSTS
- Секреты вне кода
