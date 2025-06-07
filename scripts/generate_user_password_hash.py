import sys
import bcrypt
password = sys.argv[1] if len(sys.argv) > 1 else "default_password"
hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
print(hashed) 