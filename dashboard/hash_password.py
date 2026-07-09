import getpass
from dashboard.auth import hash_password

if __name__ == '__main__':
    password = getpass.getpass('Dashboard password: ')
    confirm = getpass.getpass('Confirm: ')
    if password != confirm:
        raise SystemExit('Passwords did not match')
    print('\nDASHBOARD_PASSWORD_HASH=' + hash_password(password))
