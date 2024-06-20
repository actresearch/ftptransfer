from O365 import Account, FileSystemTokenBackend
import startbatch
from datetime import datetime

import threading

credentials = ('REDACTED_CLIENT_ID', 'REDACTED_CLIENT_SECRET')

# the default protocol will be Microsoft Graph
token_backend = FileSystemTokenBackend(token_path='my_folder', token_filename='my_token.txt')
account = Account(credentials, auth_flow_type='credentials', tenant_id='REDACTED_TENANT_ID',
                  token_backend=token_backend)

currentMonth = datetime.now().month
currentYear = datetime.now().year

def my_function():
    if account.authenticate():

        print('Authenticated!')
        mailbox = account.mailbox('jrobinson@actresearch.net')
        inbox = mailbox.inbox_folder()

        for message in inbox.get_messages():
            messagetocheck = str(message)

            if "U.S. Trailers Report" in messagetocheck:
                startbatch.runbatch()
                print("true")
            print(message)

def run_function():
    thread = threading.Timer(60.0, run_function) # 60 seconds = 1 minute
    thread.start()
    my_function()






run_function() # start the timer



#account.authenticate()

