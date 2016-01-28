import itertools, datetime, json
from time import strftime
from config import warboard_log

def log_errors(error):
    lf = open(warboard_log, 'a')
    current_time = strftime("%d-%m-%Y %H:%M:%S: ")
    lf.write(current_time+error+'\n')
    lf.close()

def chain_results(results):
    return(list(itertools.chain(*results))) # This chains all results together into one tuple

def refresh_time():
    now = datetime.datetime.now()
    if now.isoweekday() in range(6, 7):
        return(60)
    elif datetime.time(hour=7) <= now.time() <= datetime.time(hour=18): # If its a working hour return a 15 second refresh rate
        return(15)
    else:
        return(60)
