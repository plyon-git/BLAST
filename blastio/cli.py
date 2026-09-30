import argparse
import getpass
from .db import init_db, transaction, audit, now
from .auth import hash_password
from .seed import seed_demo

def main():
    parser=argparse.ArgumentParser(description='101XVC Blastio administration')
    sub=parser.add_subparsers(dest='command',required=True)
    init=sub.add_parser('init');init.add_argument('--email',required=True)
    user=sub.add_parser('create-user');user.add_argument('--email',required=True);user.add_argument('--role',choices=['admin','reviewer','operator','viewer'],default='operator')
    sub.add_parser('demo')
    args=parser.parse_args();init_db()
    if args.command in ('init','create-user'):
        password=getpass.getpass('New password (12+ characters): ')
        if getpass.getpass('Confirm password: ')!=password:parser.error('Passwords do not match')
        with transaction() as conn:
            if args.command=='init' and conn.execute('SELECT 1 FROM users LIMIT 1').fetchone():parser.error('Already initialized; use create-user')
            role='admin' if args.command=='init' else args.role
            cur=conn.execute('INSERT INTO users(email,password_hash,role,created_at) VALUES(?,?,?,?)',(args.email.lower(),hash_password(password),role,now()))
            audit(conn,'local administrator','user.created','user',cur.lastrowid,{'email':args.email.lower(),'role':role})
        print('User created. Start the application and sign in.')
    elif args.command=='demo':
        with transaction() as conn:result=seed_demo(conn)
        print('Synthetic demo created:',result)

if __name__=='__main__':main()
