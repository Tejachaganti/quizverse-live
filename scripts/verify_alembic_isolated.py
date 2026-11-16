import os
import tempfile
from alembic.config import Config
from alembic import command

f = os.path.join(tempfile.gettempdir(), 'test_alembic_95.db')
if os.path.exists(f):
    os.remove(f)

url = 'sqlite:///' + f.replace('\\', '/')
os.environ['DATABASE_URL'] = url
cfg = Config('alembic.ini')
cfg.set_main_option('sqlalchemy.url', url)

print("Starting isolated Alembic migration test...")
command.upgrade(cfg, 'head')
print("UPGRADE HEAD: SUCCESS")

command.downgrade(cfg, '-1')
print("DOWNGRADE -1: SUCCESS")

command.upgrade(cfg, 'head')
print("RE-UPGRADE HEAD: SUCCESS")

if os.path.exists(f):
    os.remove(f)
print("ISOLATED ALEMBIC VERIFICATION PASSED COMPLETELY!")
