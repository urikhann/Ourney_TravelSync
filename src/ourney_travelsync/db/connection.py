"""Environment-only credentials. Never print DSN or database exception details."""
import os
import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

def connect():
    load_dotenv(dotenv_path=os.path.join(os.getcwd(), ".env"))
    url = os.environ.get('DATABASE_URL')
    if url:
        return psycopg.connect(url, row_factory=dict_row, connect_timeout=10)
    required = ('DB_HOST', 'DB_NAME', 'DB_USER', 'DB_PASSWORD')
    if any(not os.environ.get(k) for k in required):
        raise ValueError('DB_CONFIG_MISSING: set DATABASE_URL or DB_HOST/DB_NAME/DB_USER/DB_PASSWORD')
    try:
        port=int(os.getenv('DB_PORT','5432'))
    except ValueError:
        raise ValueError('INVALID_DB_PORT') from None
    return psycopg.connect(host=os.environ['DB_HOST'], port=port,
                          dbname=os.environ['DB_NAME'], user=os.environ['DB_USER'],
                          password=os.environ['DB_PASSWORD'], sslmode=os.getenv('DB_SSLMODE', 'prefer'),
                          row_factory=dict_row, connect_timeout=10)


def safe_db_error(exc):
    # Do not expose server detail, DSN, statement parameters or supplied values.
    state=getattr(exc,'sqlstate',None) or 'CONNECTION'
    reasons={'23505':'DUPLICATE_KEY','23503':'FOREIGN_KEY_REFERENCE_MISSING',
             '23514':'CHECK_OR_STATE_CONSTRAINT','23502':'REQUIRED_FIELD_NULL',
             '22P02':'INVALID_VALUE_TYPE','CONNECTION':'CHECK_CONNECTION_SETTINGS'}
    constraint=getattr(getattr(exc,'diag',None),'constraint_name',None)
    import re
    label=(' constraint='+constraint) if constraint and re.fullmatch(r'[a-zA-Z0-9_]+',constraint) else ''
    return f'DATABASE_ERROR sqlstate={state} reason={reasons.get(state,"DATABASE_OPERATION_FAILED")}{label}'
