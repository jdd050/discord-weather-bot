import requests
import mysql.connector
import asyncio
import time
import os
import re
from dotenv import load_dotenv
from os.path import join, dirname

class RateLimitError(BaseException):
    def __init__(self, wait_time: float):
        super().__init__(f"Rate limited. Try again in {wait_time:.1f} seconds.")

class APIError(BaseException):
    def __init__(self, status_code: int):
        super().__init__(f"Return status {status_code} was recieved from NWS API")

class UnknownError(BaseException):
    def __init__(self, message: str):
        super().__init__(message)

class NWS:
    def __init__(self):
        # Fields
        self.elapsed_seconds = 0
        self._stop_event = asyncio.Event()
        self.last_request_time = 0
        self.request_cooldown = 10
        
        # Methods
        load_dotenv(join(dirname(__file__), "env\.env"))
        self.__login_geoloc_database()
    
    async def __record_time_since_req(self):
        """Async timer that runs in the background."""
        self.elapsed_seconds = 0
        self._stop_event.clear()
        
        while not self._stop_event.is_set():
            await asyncio.sleep(1.0)
            self.elapsed_seconds += 1
    
    def __stop_timer(self):
        """Stop the timer."""
        self._stop_event.set()
    
    def __check_time(self):
        # Enforce rate limit
        current_time = time.time()
        time_since_last_req = current_time - self.last_request_time
        
        # Make sure cooldown is over
        if time_since_last_req < self.request_cooldown:
            wait_time = self.request_cooldown - time_since_last_req
            raise RateLimitError(wait_time)
        
        # Start timing this request
        self.elapsed_seconds = 0
        self._stop_event.clear()
        self.timer_task = asyncio.create_task(self.__record_time_since_req())
    
    def __login_geoloc_database(self):
        """Login to the geo-location database."""
        # Set login credentials
        db_config = {
            "host":"localhost",
            "user":os.getenv("GEOLOCUSER"),
            "password":os.getenv("GEOLOCPASS"),
            "database":os.getenv("GEOLOCDB_NAME")
        }
        # make connection to database
        self.geoloc_db = mysql.connector.connect(**db_config)
        return
    
    async def countyid_from_city(self, city_name: str, state_code: str) -> str:
        """Fetch the county ID from city and state, with request rate limiting."""
        # Check cooldown
        self.__check_time()

        # Execute DB query
        query = """
        SELECT latitude, longitude
        FROM locations
        WHERE city = %s AND state_code = %s
        """
        with self.geoloc_db.cursor() as cursor:
            cursor.execute(query, (city_name, state_code))
            row = cursor.fetchone()
            cursor.fetchall()
            cursor.close()
        
        if row is None:
            self.__stop_timer()
            raise UnknownError(f"{city_name}, {state_code} not found in geolocation database")

        # Make API request to NWS
        try:
            api_res = requests.get(f"https://api.weather.gov/points/{row[0]},{row[1]}")
            if api_res.status_code == 200:
                self.last_request_time = time.time()  # Update the last request time
                api_res = api_res.json()
                county_id = re.search(r"([a-zA-Z]+\d+)", api_res["properties"]["county"])
                self.__stop_timer()
                if not county_id:
                    raise UnknownError("County ID not found in API response.")
                return county_id.group(0)
            else:
                raise APIError(api_res.status_code)
        except Exception as e:
            with open("api_err_log.txt", 'a') as f:
                f.write(f"{time.strftime("%Y-%m-%d %H:%M:%S")} - {str(e)}\n")
                f.close()
            raise UnknownError("Could not parse NWS API response. Please contact dev to check error logs")
        finally:
            self.__stop_timer()
            if self.timer_task:
                self.timer_task.cancel()
    
    def check_active_alerts(self, state_code: str = None, county_code: str = None):
        # Make sure arguments are legal
        if (state_code is None) and (county_code is None):
            raise UnknownError("You must provide an argument in the form of a state code or county code")
        elif (state_code is not None) and (county_code is not None):
            raise UnknownError("You may only provide one argument")
        
        # Check cooldown
        self.__check_time()
        
        # Fetch active alerts
        if state_code is not None:
            try:
                api_res = requests.get(f"https://api.weather.gov/alerts/active/area/{state_code}")
                if api_res.status_code == 200:
                    self.last_request_time = time.time()
                    api_res = api_res.json()
                    self.__stop_timer()
                    return api_res
                else:
                    raise APIError(api_res.status_code)
            except Exception as e:
                with open("api_err_log.txt", 'a') as f:
                    f.write(f"{time.strftime("%Y-%m-%d %H:%M:%S")} - {str(e)}\n")
                    f.close()
                raise UnknownError("Could not parse NWS API response. Please contact dev to check error logs")
            finally:
                self.__stop_timer()
                if self.timer_task:
                    self.timer_task.cancel()
        else:
            try:
                api_res = requests.get(f"https://api.weather.gov/alerts/active/zone/{county_code}")
                if api_res.status_code == 200:
                    self.last_request_time = time.time()
                    api_res = api_res.json()
                    self.__stop_timer()
                    return api_res
                else:
                    raise APIError(api_res.status_code)
            except Exception as e:
                with open("api_err_log.txt", 'a') as f:
                    f.write(f"{time.strftime("%Y-%m-%d %H:%M:%S")} - {str(e)}\n")
                    f.close()
                raise UnknownError("Could not parse NWS API response. Please contact dev to check error logs")
            finally:
                self.__stop_timer()
                if self.timer_task:
                    self.timer_task.cancel()
