from fastapi import FastAPI, BackgroundTasks, HTTPException, Response, status, Request
from fastapi.middleware.cors import CORSMiddleware
import os
from dotenv import load_dotenv
import mysql.connector
import requests
from datetime import datetime, timedelta
import time
import logging

app = FastAPI()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Add CORS middleware to allow requests from the frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # Update to your frontend's URL
    allow_credentials=True,
    allow_methods=["POST", "GET"],  # Specify methods used in availability
    allow_headers=["*"],
    expose_headers=["Location"],  # Needed if your response headers include "Location"
)

# Constants for external microservices
USER_MICROSERVICE_URL = "http://52.23.233.221:8000"
RESTAURANT_MICROSERVICE_URL = "http://34.207.95.163:8000"

# API key
API_KEY = 'VbWk7s3L4KiK5fzlO7JD3Q5EYolJI7n5'
load_dotenv()

# In-memory store for results and status tracking
availability_results = {}
task_status = {}

# Connect to the MySQL database
def connect_to_database():
    return mysql.connector.connect(
        host='availability-database.cb821k94flru.us-east-1.rds.amazonaws.com',
        user='root',
        password='dbuserdbuser',
        database='availability',
    )

# Function to make API requests
def make_get_request(url, params):
    headers = {
        'Authorization': f'ResyAPI api_key="{API_KEY}"',
        'Origin': 'https://resy.com',
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/94.0.4606.81 Safari/537.36'
        ),
    }
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Error making GET request to {url}: {e}")
        return None

# Fetch available days
def fetch_available_days(venue_id, num_seats=2):
    today = datetime.now().strftime('%Y-%m-%d')
    end_date = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')

    url = 'https://api.resy.com/4/venue/calendar'
    params = {
        'venue_id': venue_id,
        'num_seats': num_seats,
        'start_date': today,
        'end_date': end_date
    }
    return make_get_request(url, params)

# Fetch available times
def fetch_available_times(venue_id, num_seats=2, day=None):
    url = 'https://api.resy.com/4/find'
    params = {
        'lat': 0,
        'long': 0,
        'day': day,
        'party_size': num_seats,
        'venue_id': venue_id
    }
    return make_get_request(url, params)

# Check availability for first available reservation
def check_availability(restaurant_code):
    available_days_data = fetch_available_days(restaurant_code, 2)
    if available_days_data and 'scheduled' in available_days_data:
        for day in available_days_data['scheduled']:
            if day['inventory']['reservation'] == 'available':
                available_slots = fetch_available_times(restaurant_code, 2, day['date'])
                if available_slots and 'results' in available_slots:
                    venues = available_slots['results'].get('venues', [])
                    for venue in venues:
                        for slot in venue.get('slots', []):
                            start_time = slot.get('date', {}).get('start')
                            if start_time:
                                logger.info(f"First available reservation for {restaurant_code} on {day['date']} at {start_time}")
                                return {"restaurant_code": restaurant_code, "date": day['date'], "time": start_time}
                            time.sleep(1)
    return {"error": "No available reservations found"}

# Background task for availability check
def check_availability_task(restaurant_code, request_id, callback_url=None):
    results = check_availability(restaurant_code)
    availability_results[request_id] = results
    task_status[request_id] = "complete"
    logger.info(f"Stored availability for request_id: {request_id}")

    if callback_url:
        try:
            response = requests.post(callback_url, json={"status": "complete", "data": results})
            response.raise_for_status()
            logger.info(f"Callback to {callback_url} successful.")
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to send callback: {e}")

# Endpoint to initiate availability check with optional callback
@app.post("/availability/{restaurant_code}", status_code=status.HTTP_202_ACCEPTED)
async def initiate_availability_check(restaurant_code: str, request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    callback_url = payload.get("callback_url")

    request_id = f"{restaurant_code}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    background_tasks.add_task(check_availability_task, restaurant_code, request_id, callback_url)
    task_status[request_id] = "processing"

    return Response(
        content=f"Request accepted. Check status at /availability/status/{request_id}",
        headers={"Location": f"/availability/status/{request_id}"},
        status_code=status.HTTP_202_ACCEPTED
    )

# Endpoint to check status
@app.get("/availability/status/{request_id}")
async def check_status(request_id: str):
    if request_id in task_status:
        current_status = task_status[request_id]
        if current_status == "complete":
            logger.info(f"Returning complete data for request_id {request_id}")
            return {"status": "complete", "data": availability_results[request_id]}
        else:
            logger.info(f"Still processing request_id {request_id}")
            return {"status": "processing", "data": None}
    else:
        logger.warning(f"Request ID {request_id} not found")
        raise HTTPException(status_code=404, detail="Request ID not found")