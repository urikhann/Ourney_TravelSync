"""Optional Streamlit UI for inspecting the explicitly active database snapshot."""


def main():
    import pandas as pd
    import streamlit as st
    from .features import FEATURE_NAMES
    from .db.connection import connect, safe_db_error
    from .db.repository import get_active_pois

    st.set_page_config(page_title='TravelSync POI Viewer', layout='wide')
    st.title('TravelSync POI Data Viewer')

    @st.cache_data(ttl=60)
    def load_data():
        with connect() as conn:
            return pd.DataFrame(get_active_pois(conn))

    if st.button('Refresh active dataset'):
        load_data.clear()
    try:
        df = load_data()
    except ValueError as exc:
        st.error(str(exc)); return
    except Exception as exc:
        st.error(safe_db_error(exc)); return
    st.metric('Active POIs', len(df))
    if df.empty:
        st.info('No active POIs. Import and explicitly activate a dataset first.')
        return
    search = st.text_input('Place name')
    if search:
        df = df[df['place_name'].str.contains(search, case=False, na=False, regex=False)]
    for feature in st.multiselect('Required features', FEATURE_NAMES):
        df = df[df[feature] == 1]
    sources = st.multiselect('Source', sorted(df['source'].dropna().unique()))
    if sources:
        df = df[df['source'].isin(sources)]
    st.write(f'{len(df):,} matching POIs')
    st.dataframe(df[['place_name', 'category_name', 'address', 'source', *FEATURE_NAMES]], height=600)
    st.bar_chart(pd.DataFrame({'Feature': FEATURE_NAMES,
                              'Count': [int(df[f].sum()) for f in FEATURE_NAMES]}), x='Feature', y='Count')
