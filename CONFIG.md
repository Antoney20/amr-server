# AMR – Project Configuration

This project uses **Poetry** for dependency management and **Django** as the backend framework.

---

## Requirements

- Python 3.14+
- Poetry

Install Poetry if missing,

```bash
pip install poetry
```

## Poetry  Setup

```bash
cd amr
poetry install
```

This project disables package installation and uses Poetry only for dependency management.

## Adding Dependencies
Add runtime dependencies:

```bash
poetry add django
```

## Run server

```bash 
poetry run python manage.py runserver
```

## Makefile

Common development commands are defined in the Makefile

```bash 
make run          
make migrate       
make makemigrations 
make test         
make format         
make lint           

```
To see all commands

```bash
make help
```

## Environment Variables

Create a .env file:

```bash
DEBUG=true
SECRET_KEY=
```
