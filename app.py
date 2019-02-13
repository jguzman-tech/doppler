#!/usr/bin/env python


"""
Desc: Main source file for the jobstats site

Authors:
    - Chance Nelson <chance-nelson@nau.edu>
    - Ian Otto <iso-ian@nau.edu>
"""


import sys
import time
from configparser import ConfigParser
from datetime import date, timedelta
from difflib import SequenceMatcher

import pygal
from flask import Flask, render_template, request, url_for, redirect

import jobstats


app = Flask(__name__)

# Get the efficiency score normalization options
config = ConfigParser()
config.read('config.ini')
 

# Get the DB args
username     = config['DB']['username']
password     = config['DB']['password']
host         = config['DB']['host']


# Init the DB connection
db = jobstats.Jobstats(host, username, password)


# Get job normalization args
config.read('config.ini')
normalize_scores_options = config['SCORES']['normalize']
normalize_by_score       = False
ideal_score              = 0

for arg in normalize_scores_options.split(' '):
    if 'score' in arg:
        normalize_by_score = True
        ideal_score        = float(config['SCORES']['ideal score'])


def getScore(data, all_scores=False):
    """
    Desc: Get an individual score from a data set. Helper function for 
          normalize()

    Args:
        data (dict): Usage data
        all_scores (bool) (optional): Give values for all usage scores

    Returns:
        Float showiung score.
        If all_scores, a dict with keys for each score.
    """
    # Get the individual memory, cpu time, and time limit usage scores
    score_memory = data['memuse'] / data['memreq']
    score_cpu    = data['cputime'] / (data['coresreq'] * data['timeuse'])
    score_time   = data['timeuse'] / data['timereq']

    # Take the mean value of these three scores
    numerator = statistics.mean([score_memory, score_cpu, score_time])

    denominator = 1
    if normalize_by_score:
        denominator = ideal_score
    
    if not all_scores:
        return (numerator / denominator) * 100
    
    else:
        return {'total': (numerator / denominator) * 100, 
                'cores': score_cpu, 'memory': score_memory, 
                'tlimit': score_time}


def normalize(data, all_scores=False, by_date=False):
    """
    Desc: Get a job score based on usage data

    Args:
        data (dict): usage data to normalize
        all_scores (bool) (optional): Return all individual scores, along with 
                                      the total
        by_date (bool) (optional): Only used in the case that the usage data
                                   is a collection of dates. Return scores by
                                   date, instead of condensing all data

    Returns:
        Normalized score based on target total score to meet in config file.
        If all_scores, a dict is returned containing all individual scores, and
        the total.

    Notes:
        This function usilizes the constant ideal_score for mean score normalization
    """
    # Data is a single point
    if 'memuse' in data:
        memory = data['memuse']  / data['memreq']
        time   = data['timeuse'] / data['timereq']
        cpu    = data['cputime']  / (data['timeuse'] * data['cpureq'])

        score = statistics.mean([memory, time, cpu]) / ideal_score

        if all_scores:
            data['total'] = score
            return data

        else:
            return score

    # Data is a collection of scores by date ({'1/1/18': {...}})
    else:
        # Normalize each day
        if by_date:
            scores = {}
            for day in data:
                scores[day] = normalize(data[date], all_scores=all_scores)

            return scores

        # Sum each day together and get one big score
        else:
            data_total = {
                'memuse'  : 0,
                'memreq'  : 0,
                'timeuse' : 0,
                'timereq' : 0,
                'cputime' : 0,
                'cpureq'  : 0
            }
            
            for day in data:
                for val in day:
                    data_total[val] += day[val]

            return normalize(data_total, all_scores=all_scores)

        

def getTop(account_type, num, since):
    """
    Desc: Get a list of the top N users in a given timeframe

    Args:
        account_type (string): 'users' or 'accounts'
        num (int): get top N users
        since (datetime): Min date to search from present to

    Returns:
        List of max top N efficient users on the cluster.
    """
    all_data_points = []
    key_func        = None
    if account_type == 'users':
        all_data_points = db.getUsers(since=since)
        key_func = lambda user, since: normalize(getStats(user=user, since=since))

    elif account_type == 'accounts':
        all_data_points = db.getAccounts(since=since)
        key_func = lambda account, since: normalize(getStats(account=account, since=since))

    return sorted(all_data_points, key=key_func)[:num]
    

def getTimeframe(days):
    """
    Desc: Get a timeframe in day count from character

    Args:
        days (char): Timeframe identifier character in [W, M, Q]

    Returns (int):
        Length of timeframe in days
    """
    if days == 'M':
        days = 31
    
    elif days == 'Q':
        days = 100

    else:
        days = 7

    return days


@app.route('/accountsboxplot.svg')
def renderAccountsBoxPlot():
    """
    Desc: Endpoint for the account lising box plot on the home page

    Args:
        days (char) (optional): Timeframe in [W, M, Q], extracted from URL
                                arguments
    """
    return renderGraph(pygal.Box, 'Account')


@app.route('/usersboxplot.svg')
def renderUsersBoxPlot():
    """
    Desc: Endpoint for the user listing box plot on the home page

    Args:
        days (char) (optional): Timeframe in [W, M, Q], extracted from URL
                                arguments
    """
    return renderGraph(pygal.Box, 'User')


@app.route('/userslinegraph.svg')
def renderUsersLineGraph():
    """
    Desc: Endpoint for the user listing line graph on the home page

    Args:
        days (char) (optional): Timeframe in [W, M, Q], extracted from URL
                                arguments
    """
    return renderGraph(pygal.Line, 'User')


@app.route('/accountslinegraph.svg')
def renderAccountsLineGraph():
    """
    Desc: Endpoint for account listing line graph on the home page

    Args:
        days (char) (optional): Timeframe in [W, M, Q], extracted from URL
                                arguments
    """
    return renderGraph(pygal.Line, 'Account')


@app.route('/clusterlinegraph.svg')
def renderClusterLineGraph():
    """
    Desc: Endpoint for cluster line graph on the home page

    Args:
        days (char) (optional): Timeframe in [W, M, Q], extracted from URL
                                arguments
    """
    return renderGraph(pygal.Line, 'cluster')


def renderGraph(graph_function, data_set):
    """
    Desc: Render pygal SVG graphs for various data sets and timelines

    Args:
        days (char) (optional): Timeframe in [W, M, Q], extracted from URL
                                arguments
        graph_function (func): Pygal function for generating the graph (box and
                               line currently officially supported)
        data_set (string): string representation of what data set to pull from
                           (supports 'cluster', 'user', and 'account')
    """
    days       = getTimeframe(request.args.get('days'))
    days_delta = 1
    
    # Only display one data point per week for timeframes above a month
    if data_set.lower() in ['user', 'account'] and days > 31:
        days_delta = 7

    # Render the line graph
    graph = graph_function(range=[0, 100], show_x_labels=False)
 
    if days == 7:
        graph.title = data_set + ' Efficiency for the Week'
    elif days == 31:
        graph.title = data_set + ' Efficiency for the Month'
    elif days == 100:
        graph.title = data_set + ' Efficiency for the Quarter'
    else:
        graph.title = data_set + ' Efficiency'

    graph.x_labels    = [date.today() - timedelta(i) for i in range(days, 0, days_delta * -1)]
    data_points       = {}

    if data_set.lower() == 'account':
        # Get the top ten accounts
        top_accounts = getTop('accounts', 10, date.today()-timedelta(days))

        # Retrieve usage data for each top account
        for account in top_accounts:
            data = db.getStats(account=account,
                               since=(date.today()-timedelta(days)),
                               by_date=True)

            data_points[account] = data

    if data_set.lower() == 'user':
        # Get the top ten users
        top_accounts = getTop('users', 10, date.today()-timedelta(days))

        # Retrieve usage data for each top account
        for account in top_accounts:
            data = db.getStats(user=user,
                               since=(date.today-timedelta(days)),
                               by_date=True)

            data_points[account] = data

    elif data_set.lower() == 'cluster':
        data = db.getStats(since=date.today()-timedelta(days), by_date=True)

        graph.add('cores', cores)
        graph.add('memory', memory)
        graph.add('time limit', tLimit)
        graph.add('total efficiency', total)

        return graph.render_response()
    
    # Add each account in alphabetical order
    for i in sorted(data_points.keys()):
        graph.add(i, [j for j in data_points[i] if j != 0])

    return graph.render_response()


@app.route('/')
@app.route('/home')
def home():
    """
    Desc: Endpoint for home page

    Args:
        view (string) (optional): What data to display in 
                                  [cluster, accounts, users]
        time (char) (optional): Timeframe in [W, M, Q], extracted from URL
                                arguments
    """
    view      = request.args.get('view')
    time      = getTimeframe(request.args.get('time'))
    timeframe = request.args.get('time')

    data_points = []

    # Retrieve stats for all accounts/users in the given timeframe
    # Default to accounts view
    if not view or view in ['cluster', 'accounts']:
        data_points = db.getAccounts()

    else:
        data_points = db.getUsers()

    job_data = []

    # Gather use data for all users/accounts. Remove from array empty ones
    for i in data_points:
        data = None
        if not view or view in ['cluster', 'accounts']:
            data = db.getStats(account=i, 
                               since=(date.today() - timedelta(time)))

        else:
            data = db.getStats(user=i, 
                               since=(date.today() - timedelta(time)))

        # Add in the account/user name
        data['owner'] = i

        if data['memreq'] and data['cpureq'] and data['timereq']:
            job_data.append(data)


    # Organize job data
    job_data = sorted(job_data, key=lambda x: normalize(x))

    account_ranks = []

    for i in job_data:
        account_name = None
        if not view or view in ['cluster', 'accounts']:
            account_name = i['account']

        else:
            account_name = i['user']

        cores   = '-'
        memory  = '-'
        t_limit = '-'
        total   = '-'
        job_sum = '-'
        
        if i['cores']:
            cores = i['cores']

        if i['memory']:
            memory = i['memory']

        if i['tlimit']:
            t_limit = i['tlimit']
        
        total = int(normalize(i))

        account_ranks.append([len(account_ranks)+1, account_name, cores,
                              memory, t_limit, total, job_sum])

    return render_template('home.html', graph=renderGraph, box=pygal.Box,
                           line=pygal.Line, account_ranks=account_ranks, 
                           view=view, time=timeframe)


@app.route('/account/<account_name>/linegraph.svg')
def renderAccountLineGraph(account_name):
    """
    Desc: Endpoint for the account-centric line graph

    Args:
        account_name (string): Account to query
        days (char) (optional): Timeframe in [W, M, Q], extracted from URL
                                arguments
    """
    days = int(request.args.get('days'))
  
    data = db.getStats(account_name, since=(date.today() - timedelta(days)), by_date=True)

    # Render the line graph
    line_graph = pygal.Line(x_label_rotation=20, range=[0, 100], show_x_labels=False)

    if days == 7:
        line_graph.title = account_name + ' Efficiency for the Week'
    elif days == 31:
        line_graph.title = account_name + ' Efficiency for the Month'
    elif days == 100:
        line_graph.title = account_name + ' Efficiency for the Quarter'
    else:
        line_graph.title = account_name + ' Efficiency'

    line_graph.x_labels = sorted(data.keys())

    cores  = [data[i]['cores']  for i in data]
    memory = [data[i]['memory'] for i in data]
    tLimit = [data[i]['tlimit'] for i in data]
    total  = [data[i]['total']  for i in data]
    
    for index, i in enumerate(total):
        if i:
            total[index] = normalize(i)

    line_graph.add('cores', cores)
    line_graph.add('memory', memory)
    line_graph.add('time limit', tLimit)
    line_graph.add('total', total)

    return line_graph.render_response()


@app.route('/account/<account_name>/userpiegraph.svg')
def renderAccountUsersGraph(account_name):
    """
    Desc: Endpoint for the accont users job distribution pie graph

    Args:
        account_name (string): Account to query
        days (char) (optional): Timeframe in [W, M, Q], extracted from URL
                                arguments
    """
    days = int(request.args.get('days'))

    users = db.getUsers(account=account_name)
    
    data = {}

    for user in users:
        data[user] = db.getUserJobCount(since=date.today()-timedelta(days))

    # Render the pie graph
    pie_graph = pygal.Pie()
    pie_graph.title = 'Jobs Per User'
    for i in sorted(data.keys()):
        pie_graph.add(i, [{
            "value": data[i],
            "xlink": url_for("viewUser", user_name=i)
        }])
    
    return pie_graph.render_response()


@app.route('/account')
@app.route('/account/<account_name>')
def viewAccount(account_name=None):
    """
    Desc: Endpoint for the account view page

    Args:
        account (string) (optional): Account to query, extracted from URL
                                     args.
        account_name (string) (optional): Account to query
        time (char) (optional): Timeframe in [W, M, Q], extracted from URL
                                arguments
    """
    # Make sure we have an account name from either the url or the args
    # It will be in the args if we're coming from a search
    if not account_name:
        # Find the most similar account to the searched name
        search = request.args.get('search')

        if not search:
            search = request.args.get('account')

        if not search:
            return redirect(url_for('home'))

        start    = time.time()
        accounts = db.getAccounts()
        start    = time.time()

        similarities = [SequenceMatcher(None, search, i).ratio() for i in accounts]
        account_name = accounts[similarities.index(max(similarities))]

    days      = getTimeframe(request.args.get('time'))
    timeframe = request.args.get('time')
    
    since = date.today() - timedelta(days)
    total = db.getAccountTotalScore(account_name, since=since)

    for i in total:
        if total[i] == 0:
            total[i] = '-'

    users = {}
    user_list = db.getAccountUsers(account_name)

    for user in user_list:
        users[user] = db.getUserStats(user, account_name, since=since)
        
        for element in users[user].keys():
            if users[user][element] == 0:
                users[user][element] = '-'

    # Render the account view template
    return render_template('account.html', account_name=account_name, users=users, total=total, time=timeframe, timeframe=days)


@app.route('/user/<user_name>/linegraph.svg')
def renderUserAccountLineGraph(user_name, account=None):
    """
    Desc: Endpoint for the account line graph, displaying indiviual user
          efficiencies

    Args:
        user_name (string): Username to query
        account_name (string) (optional): Account to query, used to narrow
                                          down search for users with multiple
                                          slurm accounts
        days (char) (optional): Timeframe in [W, M, Q], extracted from URL
                                arguments
    """
    days    = int(request.args.get('days'))
    account = request.args.get('account')

    data = db.getStats(user_name, account, 
                           since=(date.today() - timedelta(days)), 
                           as_list=True)

    # Render the line graph
    line_graph = pygal.Line(x_label_rotation=20, range=[0, 100], show_x_labels=False)
    
    if days == 7:
        line_graph.title = user_name + ' Efficiency for the Week'
    elif days == 31:
        line_graph.title = user_name + ' Efficiency for the Month'
    elif days == 100:
        line_graph.title = user_name + ' Efficiency for the Quarter'
    else:
        line_graph.title = user_name + ' Efficiency'


    line_graph.x_labels = sorted([i[2] for i in data])
    
    cores   = []
    memory  = []
    t_limit = []
    total   = []
    
    for i in data:
        cores.append(i[3])
        memory.append(i[4])
        t_limit.append(i[5])
        total.append(i[6])
   
    for index, i in enumerate(total):
        if i:
            total[index] = normalize(i)

    line_graph.add('cores', cores)
    line_graph.add('memory', memory)
    line_graph.add('time limit', t_limit)
    line_graph.add('total', total)

    return line_graph.render_response()


@app.route('/user')
@app.route('/user/<user_name>')
def viewUser(user_name=None):
    """
    Desc: Endpoint for the user view page 

    Args:
        user_name (string) (optional): Username to query
        search (string) (optional): Username to query, extracted from URL args
        days (char) (optional): Timeframe in [W, M, Q], extracted from URL
                                arguments
    """
    # Make sure we have an account name from either the url or the args
    # It will be in the args if we're coming from a search
    if not user_name:
        # Find the most similar account to the searched name
        search = request.args.get('search')

        if not search:
            return redirect(url_for('home'))

        users        = db.getUsers()
        similarities = [SequenceMatcher(None, search, i).ratio() for i in users]
        user_name    = users[similarities.index(max(similarities))]
 
    days      = getTimeframe(request.args.get('time'))
    timeframe = request.args.get('time')

    since = date.today() - timedelta(days)

    accounts = {}

    for account in db.getAccounts(username=user_name):
        accounts[account[0]] = db.getStats(username=user_name, account=account[0], since=since)

    # Render the account view template
    return render_template('user.html', user_name=user_name, accounts=accounts, time=timeframe, timeframe=days)


if __name__ == '__main__':
    if '--debug' in sys.argv:
        app.run(debug=True, threaded=True, host='0.0.0.0')

    else:
        app.run()
