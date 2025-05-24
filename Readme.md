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