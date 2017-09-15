import requests
import json
import ast
import time
from redis_functions import set_data, get_data, delete_data
from misc import log_messages, chain_results
from config import newrelic_insights_endpoint, newrelic_insights_timeout, newrelic_main_and_insights_keys, newrelic_infrastructure_max_data_age, newrelic_main_api_violation_endpoint, newrelic_main_api_timeout

# This module assumes that newrelic insights returns the most recent data first
def store_newrelic_infra_data():
    """
    Calls get_infra_data and puts the relavent structured data into redis
    """
    infra_results = {}
    infra_results['failed_newrelic_infra_accounts'] = 0
    infra_results['total_newrelic_infra_accounts'] = 0
    infra_results['total_checks'] = 0
    infra_results['successful_checks'] = 0
    reporting_server_names = []
    for account in newrelic_main_and_insights_keys:
        account_results = []
        all_server_names = []
        infra_results['total_newrelic_infra_accounts'] += 1
        number_or_hosts_url = '{}{}/query?nrql=SELECT%20uniqueCount(fullHostname)%20FROM%20SystemSample'.format(newrelic_insights_endpoint, newrelic_main_and_insights_keys[account]['account_number'])
        try:
            number_of_hosts_response = requests.get(number_or_hosts_url, headers={'X-Query-Key': newrelic_main_and_insights_keys[account]['insights_api_key']}, timeout=newrelic_insights_timeout)
            number_of_hosts_response.raise_for_status()
        except requests.exceptions.RequestException:
            infra_results['failed_newrelic_infra_accounts'] += 1
            log_messages('Could not get NewRelic Infrastructure data for {} error'.format(account))
            continue

        # It may be possible for 3 servers to be found, one of which has not
        # reported for a long time and that when limiting by number of results
        # two responses are recieved for one server, one for another and none
        # for the third, the code doesn't currently check this and I expect it
        # would pass both results through and cause duplicate rows on the
        # warboard
        number_of_hosts_data = json.loads(number_of_hosts_response.text)
        number_of_hosts = number_of_hosts_data['results'][0]['uniqueCount']
        metric_data_url = '{}{}/query?nrql=SELECT%20displayName%2C%20fullHostname%2C%20cpuPercent%2C%20memoryUsedBytes%2C%20memoryTotalBytes%2C%20diskUtilizationPercent%2C%20diskUsedPercent%2C%20timestamp%20FROM%20SystemSample%20LIMIT%20{}'.format(newrelic_insights_endpoint, newrelic_main_and_insights_keys[account]['account_number'], number_of_hosts)
        try:
            metric_data_response = requests.get(metric_data_url, headers={'X-Query-Key': newrelic_main_and_insights_keys[account]['insights_api_key']}, timeout=newrelic_insights_timeout)
            metric_data_response.raise_for_status()
        except requests.exceptions.RequestException:
            infra_results['failed_newrelic_infra_accounts'] += 1
            log_messages('Could not get NewRelic Infrastructure data for {} error'.format(account))
            continue

        account_infra_data = json.loads(metric_data_response.text)
        try:
            violation_data_response = requests.get(newrelic_main_api_violation_endpoint, headers={'X-Api-Key': newrelic_main_and_insights_keys[account]['main_api_key']}, timeout=newrelic_main_api_timeout)
            violation_data_response.raise_for_status()
        except requests.exceptions.RequestException:
            infra_results['failed_newrelic_infra_accounts'] += 1
            log_messages('Could not get NewRelic Alerts violation data for {} error'.format(account))
            continue

        violation_data = json.loads(violation_data_response.text)['violations']
        for num, host_data in enumerate(account_infra_data['results'][0]['events']):
            infra_results['total_checks'] += 1
            infrastructure_host = {}
            # name is the display name, if it is not set it is the hostname
            # I will crop the name in the jinja filter
            infrastructure_host['name'] = account_infra_data['results'][0]['events'][num]['fullHostname']
            if account_infra_data['results'][0]['events'][num]['displayName']:
                infrastructure_host['name'] = account_infra_data['results'][0]['events'][num]['displayName']

            reporting_server_names.append(infrastructure_host['name'])

            # The warboard script will check this was in the last 5 minutes
            # and react acordingly - set to blue order by 0
            # It will make it's own api call to avoid using different timezones
            # and converting from how newrelic want to format the time
            infrastructure_host['timestamp'] = account_infra_data['results'][0]['events'][num]['timestamp']
            # The warboard will need to have a list of hosts and check if they
            # are no-longer present since this will be overwriting the key in
            # redis when it gets a response for half of the hosts/accounts

            # data we are interested in needs to be in a format similar to
            # newrelic servers in order to easily be displayed along side it
            memory_percentage = None
            if account_infra_data['results'][0]['events'][num]['memoryUsedBytes'] != None and account_infra_data['results'][0]['events'][num]['memoryTotalBytes'] != None:
                memory_percentage = ( account_infra_data['results'][0]['events'][num]['memoryUsedBytes'] / account_infra_data['results'][0]['events'][num]['memoryTotalBytes'] ) * 100

            infrastructure_host['summary'] = {
                'memory': memory_percentage,
                'disk_io': account_infra_data['results'][0]['events'][num]['diskUtilizationPercent'],
                'fullest_disk': account_infra_data['results'][0]['events'][num]['diskUsedPercent'],
                'cpu': account_infra_data['results'][0]['events'][num]['cpuPercent'] }

            # The warboard script will check this was in the last 5 minutes
            # and react acordingly - set to blue order by 0
            # The warboard will need to have a list of hosts and check if they
            # are no-longer present since this will be overwriting the key in
            # redis when it gets a response for half of the hosts/accounts

            # Setting the orderby using the same field as newrelic servers
            infrastructure_host['orderby'] = max(
                    infrastructure_host['summary']['cpu'],
                    infrastructure_host['summary']['memory'],
                    infrastructure_host['summary']['fullest_disk'],
                    infrastructure_host['summary']['disk_io'])
            if infrastructure_host['orderby'] == None:
                infrastructure_host['orderby'] = 0
                infrastructure_host['health_status'] = 'blue'

            # Using violation data to determine the health status of servers
            violation_level = 0
            # violation level 0 is green and no violation
            # violation level 1 is orange and Warning
            # violation level 2 is red and Critical
            # I'm giving it a number to make comparisons easier
            for violation in violation_data:
                if violation['entity']['product'] != 'Infrastructure':
                    continue

                # We have the option to just flag all servers in the account
                # orange or red based on Warning or Critical here
                # This would be a consistantly wrong behavior (the best kind of
                # wrong)
                # The issue is that in my testing servers are using names of
                # '<fullhostname> (/)' why they don't just use <fullhostname>
                # is beyond me, I am unsure on if this tracks display names

                # The best I can do to match check if the server / host we are
                # currently checking was the cause of the violation we are
                # currently looping through
                if infrastructure_host['name'] in violation['entity']['name']:
                    if violation['priority'] == 'Warning':
                        if violation_level < 1:
                            violation_level = 1
                    elif violation['priority'] == 'Critical':
                        if violation_level < 2:
                            violation_level = 2
                    else:
                        # I'm not expecting this to happen and if I make the server red it will confuse people, it would be nice to be able to make servers pink or send emails since I doubt the log will be read
                        log_messages('Warning: unrecognised violation {} expected Warning or Critical'.format(violation['priority']), 'error')

            infrastructure_host['health_status'] = 'blue'
            if violation_level == 0:
                infrastructure_host['health_status'] = 'green'
            elif violation_level == 1:
                infrastructure_host['health_status'] = 'orange'
            elif violation_level == 2:
                infrastructure_host['health_status'] = 'red'

            infra_results['successful_checks'] += 1
            account_results.append(infrastructure_host)

        set_data('resources_newrelic_infra_'+account, account_results)

    all_server_names_data = get_data('resources_server_names_newrelic_infrastructure')
    if all_server_names_data == None or all_server_names_data == 'None' or type(all_server_names_data) != str:
        all_server_names = []
    else:
        all_server_names = ast.literal_eval(all_server_names_data)

    updated_all_server_names = list(set(all_server_names + reporting_server_names))
    set_data('resources_server_names_newrelic_infrastructure', updated_all_server_names)
    set_data('resources_success_newrelic_infrastructure', infra_results)

def get_newrelic_infra_results():
    """
    Pulls Infrastructure data from redis and checks for missing hosts/accounts
    and formats it ready to be merged with other resource modules by the calling
    function
    """
    all_infra_checks = []
    infra_results_string = get_data('resources_success_newrelic_infrastructure')
    # Not going to check resources_success_newrelic_infrastructure is present
    # because if it isn't then we have a bigger problem, I want to put some
    # error handling higher up so that when say infrastructure of sirportly
    # is broken it alerts you but displays a big warning, for now I'll leave
    # this as causing a 500 error since any other unseen issues will also do
    infra_results = ast.literal_eval(infra_results_string)
    # Moving the colour count to the display function because I would like to
    # set it as late as possible to avoid having to subtract from values when
    # changing the health status since I feel that makes the code less clear
    infra_results['red'] = 0
    infra_results['orange'] = 0
    infra_results['green'] = 0
    infra_results['blue'] = 0
    all_server_names_data = get_data('resources_server_names_newrelic_infrastructure')
    if all_server_names_data == None or all_server_names_data == 'None' or type(all_server_names_data) != str:
        all_server_names = []
    else:
        all_server_names = ast.literal_eval(all_server_names_data)

    reporting_server_names = []
    for account in newrelic_main_and_insights_keys:
        # I need to retrieve the list differently or store it dirrerently
        account_checks_string = get_data('resources_newrelic_infra_{}'.format(account))
        retrieved_data_time = time.time()
        if account_checks_string == None or account_checks_string == 'None' or type(account_checks_string) != str:
            infra_results['failed_newrelic_infra_accounts'] += 1
            continue

        account_checks_data_list = ast.literal_eval(account_checks_string)
        # the code needs to check the age of the data to make sure it is not old
        # it also needs to check for hosts that have vanished and deal with them
        # they need to be blue with order by 0, it needs the way it is checking
        # this to have a timeout of say a week in redis keys or I need to add a
        # section to the prune keys file

        for infrastructure_host in account_checks_data_list:
            reporting_server_names.append(infrastructure_host['name'])
            # NewRelic Insights returns the timestamp as milli-seconds since
            # epoch, I am converting everything to seconds
            if retrieved_data_time - ( infrastructure_host['timestamp'] / 1000 ) > newrelic_infrastructure_max_data_age:
                # Set servers that haven't reported within
                # newrelic_infrastructure_max_data_age seconds to blue and
                # orderby to 0
                # The number of each colour should be counted at the end
                # rather than added to as we go since it would be easier to
                # maintain
                infrastructure_host['health_status'] = 'blue'
                infrastructure_host['orderby'] = 0

            if infrastructure_host['health_status'] == 'green':
                infra_results['green'] += 1
            elif infrastructure_host['health_status'] == 'blue':
                infra_results['blue'] += 1
            elif infrastructure_host['health_status'] == 'orange':
                infra_results['orange'] += 1
            elif infrastructure_host['health_status'] == 'red':
                infra_results['red'] += 1

        all_infra_checks.append(account_checks_data_list)

    infra_results['checks'] = chain_results(all_infra_checks) # Store all the NewRelic Infrastructure results as 1 chained list
    unreporting_server_names = list(set(all_server_names) - set(reporting_server_names))
    for infrastructure_host in unreporting_server_names:
        infra_results['checks'].append({'name': infrastructure_host, 'health_status': 'blue', 'orderby': 0})

    return infra_results

def clear_new_relic_infrastructure_server_list():
    delete_data('resources_server_names_newrelic_infrastructure')
