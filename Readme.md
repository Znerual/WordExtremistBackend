# Instructions
Start the Postgres server using 
```bash
docker run --name word_extremist_db -e POSTGRES_PASSWORD=password -p 5432:5432 postgres
```

Setup user in postgres using psql
```bash
docker exec -it word_extremist_db psql -U postgres
create database word_extremist_db;
\c word_extremist_db;
```


Start backend using 
```bash
uvicorn app.main:app --reload --host 0.0.0.0
```

# Database migration
Alembic is installed, we first set it up using
alembic init alembic

in the alembic.ini, set the sqlalchemy.url path to the database url
Adapt the env.py file in the alembic directory (only need to do this once)
Then create the revision like this:

alembic revision -m "add_client_provided_id_to_users_table" --autogenerate

Check the revision in the alembic/versions folder and then apply it using
alembic upgrade head