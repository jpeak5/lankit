#!/opt/lankit-portal/venv/bin/python
import sys

sys.path.insert(0, "/opt/lankit-portal")
import db

try:
    import speedtest as st_lib

    st = st_lib.Speedtest(secure=True)
    st.get_best_server()
    st.download()
    st.upload()
    r = st.results.dict()
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO speed_results (download_mbps, upload_mbps, ping_ms) VALUES (?, ?, ?)",
            (r["download"] / 1e6, r["upload"] / 1e6, r["ping"]),
        )
except Exception as e:
    with db.get_db() as conn:
        conn.execute("INSERT INTO speed_results (error) VALUES (?)", (str(e),))
