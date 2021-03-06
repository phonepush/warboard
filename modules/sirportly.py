import requests, math
from redis_functions import set_data, get_data
from config import sirportly_key, sirportly_token, sirportly_users, sirportly_endpoint, sirportly_red_filter, sirportly_resolved_filter
from config import sirportly_total_filter, sirportly_reduser_filter, sirportly_greenuser_filter, sirportly_blueuser_filter, sirportly_unassigned_filter

def sirportly_filter(filterid, user): # This is used to get data for a specific filter in sirportly, it accepts a filterID and a username/False if no user needed.
    if user != False:
        endpoint = sirportly_endpoint+'/filter?filter='+filterid+'&user='+user
    else:
        endpoint = sirportly_endpoint+'/filter?filter='+filterid
    try:
        r = requests.get(endpoint, headers={'X-Auth-Token': sirportly_token, 'X-Auth-Secret': sirportly_key})
        if r.status_code != requests.codes.ok:
            raise requests.exceptions.RequestException
        return(r.json()['pagination']['total_records'])
    except requests.exceptions.RequestException:
        return(0)

def get_sirportly_data(): # Get all the data we need from sirportly and store it in a dict
    sirportly_data = {'users': {}}
    sirportly_data['unassigned_tickets'] = sirportly_filter(sirportly_unassigned_filter, False)
    sirportly_data['total_tickets'] = sirportly_filter(sirportly_total_filter, False)
    sirportly_data['red_tickets'] = sirportly_filter(sirportly_red_filter, False)
    sirportly_data['resolved_tickets'] = sirportly_filter(sirportly_resolved_filter, False)
    for user in sirportly_users:
        sirportly_data['users'][user+'_red'] = sirportly_filter(sirportly_reduser_filter, user)
        sirportly_data['users'][user+'_green'] = sirportly_filter(sirportly_greenuser_filter, user)
        sirportly_data['users'][user+'_blue'] = sirportly_filter(sirportly_blueuser_filter, user)
        sirportly_data['users'][user+'_total'] = sirportly_data['users'][user+'_red']+sirportly_data['users'][user+'_green']+sirportly_data['users'][user+'_blue']
    sirportly_data['multiplier'] = sirportly_ticket_multiplier(sirportly_data['unassigned_tickets'], sirportly_data['users'])
    try:
        sirportly_data['red_percent'] = math.ceil(100*float(sirportly_data['red_tickets'])/float(sirportly_data['total_tickets']))
    except ZeroDivisionError: # We will get a ZeroDivisionError generally if both of these ticket counts are 0 (normally when an API error has occured)
        sirportly_data['red_percent'] = 0
    sirportly_data['green_percent'] = 100-sirportly_data['red_percent']
    return(sirportly_data)

def sirportly_ticket_multiplier(unassigned, user_data): # Calculate the multiplier needed for the length of the bars on the warboard
    ticket_counts = [unassigned]
    for user in sirportly_users:
        ticket_counts.append(user_data[user+'_total'])

    most_tickets = max(ticket_counts)
    multiplier = 1
    if most_tickets > 0:
        multiplier = 100.0 / most_tickets

    return(multiplier)

def store_sirportly_results(): # Store all the sirportly data in redis
    sirportly_data = get_sirportly_data()
    for key in sirportly_data:
        if key == 'users':
            for key in sirportly_data['users']:
                set_data(key, sirportly_data['users'][key])
        else:
            set_data(key, sirportly_data[key])

def get_sirportly_results(): # Get all the sirportly data to pass to the warboard, some things need to be ints for Jinja2 to do calcs
    sirportly_results = {'users': {}}
    sirportly_results['unassigned_tickets'] = int(get_data('unassigned_tickets'))
    sirportly_results['resolved_tickets'] = int(get_data('resolved_tickets'))
    sirportly_results['red_percent'] = get_data('red_percent')
    sirportly_results['green_percent'] = get_data('green_percent')
    sirportly_results['multiplier'] = float(get_data('multiplier'))
    sirportly_results['red_tickets'] = get_data('red_tickets')
    sirportly_results['total_tickets'] = get_data('total_tickets')
    for user in sirportly_users:
        sirportly_results['users'][user+'_red'] = int(get_data(user+'_red'))
        sirportly_results['users'][user+'_green'] = int(get_data(user+'_green'))
        sirportly_results['users'][user+'_blue'] = int(get_data(user+'_blue'))
        sirportly_results['users'][user+'_total'] = get_data(user+'_total')
    return(sirportly_results)
