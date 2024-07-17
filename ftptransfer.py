from O365 import Account, FileSystemTokenBackend, mailbox
import time
import runjson
import startbatch
from datetime import datetime, timedelta
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
currentDay = datetime.now().day
threeminago = datetime.now() - timedelta(minutes=3)


#Path = "C:/Users/ITGURU/PycharmProjects/JSON/"
#Path = "C:/Users/ITGURU/PycharmProjects/JSON/"
def my_function():
    if account.authenticate():

        print('Authenticated!')
        mailbox = account.mailbox('jrobinson@actresearch.net')
        inbox = mailbox.inbox_folder()
        #query = mailbox.new_query()
        #queryresult = query.on_attribute('created_date_time').greater(
            #datetime(threeminago.year, threeminago.month, threeminago.day, threeminago.hour, threeminago.minute))
        #filtered_messages = mailbox.get_messages(query=queryresult)

        for message in mailbox.get_messages():
            messagetocheck = str(message)


            if "U.S. Trailers Report" in messagetocheck:
                startbatch.runbatch()
                print(message)
                message.delete()

            if "Transportation Digest" in messagetocheck:
                startbatch.runbatch()
                print(message)
                message.delete()



            if "Commercial Vehicle Dealer Digest" in messagetocheck:
                startbatch.runbatch()
                print(message)
                message.delete()

            if "Commercial Vehicle Preliminary Net Orders" in messagetocheck:
                #this needs harcoded or set using above Path variable, also this needs to match JSON py directory for email location
                message.save_as_eml(to_path=Path('C:/Users/ACTServer1/Services/JSON/Commercial Vehicle Preliminary Net Orders.eml'))
                time.sleep(5)
                runjson.runbatch()
                print("JSON Ran Successfully")
                message.delete()



            if "U.S. Trailer Prelim Net Orders" in messagetocheck:
                #this needs harcoded or set using above Path variable, also this needs to match JSON py directory for email location
                message.save_as_eml(to_path=Path('C:/Users/ACTServer1/Services/JSON/U.S. Trailer Prelim Net Orders.eml'))
                time.sleep(5)
                runjson.runbatch2()
                print("JSON Ran Successfully")
                message.delete()

def run_function():
    thread = threading.Timer(60.0, run_function) # 60 seconds = 1 minute
    thread.start()
    my_function()






run_function() # start the timer



#account.authenticate()

