from O365 import Account, FileSystemTokenBackend, mailbox
import time
import runjson
import startbatch
from datetime import datetime
from pathlib import Path

import threading



#documentation -- https://github.com/O365/python-o365

credentials = ('REDACTED_CLIENT_ID', 'REDACTED_CLIENT_SECRET')

# the default protocol will be Microsoft Graph
token_backend = FileSystemTokenBackend(token_path='my_folder', token_filename='my_token.txt')
account = Account(credentials, auth_flow_type='credentials', tenant_id='REDACTED_TENANT_ID',
                  token_backend=token_backend)

currentMonth = datetime.now().month
currentYear = datetime.now().year
#Path = "C:/Users/ITGURU/PycharmProjects/JSON/"
#Path = "C:/Users/ITGURU/PycharmProjects/JSON/"
def my_function():
    if account.authenticate():

        print('Authenticated!')
        mailbox = account.mailbox('jrobinson@actresearch.net')
        inbox = mailbox.inbox_folder()

        for message in inbox.get_messages():
            messagetocheck = str(message)

            if "U.S. Trailers Report" in messagetocheck:
                startbatch.runbatch()
                print(message)

            if "Transportation Digest" in messagetocheck:
                startbatch.runbatch()
                print(message)



            if "Commercial Vehicle Dealer Digest" in messagetocheck:
                startbatch.runbatch()
                print(message)

            if "Commercial Vehicle Preliminary Net Orders" in messagetocheck:
                #this needs harcoded or set using above Path variable, also this needs to match JSON py directory for email location
                message.save_as_eml(to_path=Path('C:/Users/ACTServer1/Services/JSON/Commercial Vehicle Preliminary Net Orders.eml'))
                time.sleep(5)
                runjson.runbatch()
                print("JSON Ran Successfully")



            if "U.S. Trailer Prelim Net Orders" in messagetocheck:
                #this needs harcoded or set using above Path variable, also this needs to match JSON py directory for email location
                message.save_as_eml(to_path=Path('C:/Users/ACTServer1/Services/JSON/U.S. Trailer Prelim Net Orders.eml'))
                time.sleep(5)
                runjson.runbatch2()
                print("JSON Ran Successfully")

def run_function():
    thread = threading.Timer(60.0, run_function) # 60 seconds = 1 minute
    thread.start()
    my_function()






run_function() # start the timer



#account.authenticate()

