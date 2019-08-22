from scripts.funcs import initiate_db, load_data_into_db

conn, cur = initiate_db("./db/acordaos-download.db")
years = list(range(2000, 2018))
load_data_into_db(years, cur)
conn.commit()
conn.close()