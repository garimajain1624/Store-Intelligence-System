import streamlit as st
import requests

st.title("Store Intelligence Dashboard")

try:
    r = requests.get("http://127.0.0.1:8000/health")

    st.write("Status Code:", r.status_code)

    try:
        st.json(r.json())
    except Exception:
        st.write(r.text)

except Exception as e:
    st.error(str(e))