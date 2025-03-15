import discord
import os
import mysql.connector
import re
from nws import NWS, RateLimitError, UnknownError, APIError
from discord.ext import commands
from os.path import join, dirname
from dotenv import load_dotenv

class Bot():
    def __init__(self) -> None:
        # Class fields
        self.prefix = '?'
        self.intents = discord.Intents.default()
        self.intents.message_content = True
        self.NWS = NWS()
        
        # Load secure env
        self.dotenv_path = join(dirname(__file__), "env\.env")
        load_dotenv(self.dotenv_path)
        
        # Create bot
        self.bot = commands.Bot(
            command_prefix=self.prefix,
            intents=self.intents
        )
        
        # Class methods
        self.__init_bot_db()
        self.add_commands()
        self.add_events()
    
    def __init_bot_db(self):
        # Set login credentials
        db_config = {
            "host":"localhost",
            "user":os.getenv("BOTUSER"),
            "password":os.getenv("BOTPASS"),
            "database":os.getenv("BOTDB_NAME")
        }
        # make connection to database
        self.bot_db = mysql.connector.connect(**db_config)
        return
    
    def __parse_state_name(self, state_name: str) -> str | None:
        with self.bot_db.cursor() as cursor:
            query = "SELECT state_code FROM server_states WHERE state_name = %s"
            cursor.execute(query, (state_name,))
            res = cursor.fetchone()
            if res is not None:
                return res[0]
        return None
        
    
    def add_commands(self):
        @self.bot.command(name="ping")
        async def ping(ctx):
            """Replies with the Discord API latency"""
            await ctx.send(f"{self.bot.latency}")
        
        @self.bot.command(name="say")
        async def say(ctx, *, message: str):
            """Repeats the user's message"""
            await ctx.send(message)      
    
    def add_events(self):
        @self.bot.event
        async def on_ready():
            """Triggered when the bot is ready"""
            print(f"Bot is online and logged in as {self.bot.user}")
            try:
                # Sync slash commands
                await self.bot.tree.sync()
                print("Slash commands synced successfully.")
            except Exception as e:
                print(f"Failed to sync slash commands. Error:\n{e}")
        
        @self.bot.tree.command(name="ping", description="Replies with the Discord API latency")
        async def slash_ping(interaction: discord.Interaction):
            """(Slash command) Replies with the Discord API latency"""
            await interaction.response.send_message(f"{self.bot.latency}", ephemeral=True)
        
        @self.bot.tree.command(name="say", description="Repeats the user's message")
        async def slash_say(interaction: discord.Interaction, message: str):
            """(Slash command) Repeats the user's message."""
            await interaction.response.send_message(message, ephemeral=True)
        
        @self.bot.tree.command(name="addstate", description="Add a state to the bot database")
        async def slash_addstate(interaction: discord.Interaction, state_code: str, state_name: str):
            """(Slash command) Adds a state and corresponding state code to the bot's database"""
            query = "INSERT IGNORE INTO server_states (state_code, state_name) VALUES (%s, %s)"
            with self.bot_db.cursor() as cursor:
                cursor.execute(query, (state_code, state_name))
                cursor.fetchall()
                cursor.close()
            self.bot_db.commit()
            await interaction.response.send_message(f"State {state_name} added to bot database with code {state_code}")
            return
        
        @self.bot.tree.command(name="removestate", description="Remove a state from the bot database")
        async def slash_removestate(interaction: discord.Interaction, state_name: str):
            """(Slash command) Remove a state and corresponding state code to the bot's database"""
            query = "DELETE FROM server_states WHERE state_name = %s"
            with self.bot_db.cursor() as cursor:
                cursor.execute(query, (state_name,))
                cursor.fetchall()
                cursor.close()
            self.bot_db.commit()
            await interaction.response.send_message(f"Deletion query for {state_name} executed successfully")
            return
        
        @self.bot.tree.command(name="addcity", description="Add the county a city is in to the bot's database")
        async def slash_addcity(interaction: discord.Interaction, city: str, state_name: str):
            """(Slash command) Adds a city and corresponding county code to the bot's database"""
            await interaction.response.defer()
            query = "INSERT IGNORE INTO server_counties (nws_county_id, city_name) VALUES (%s, %s)"
            state_code = self.__parse_state_name(state_name)
            county_code = ""
            if state_code is not None:                
                try:
                    county_code = await self.NWS.countyid_from_city(city, state_code)
                except (RateLimitError, UnknownError, APIError) as e:
                    await interaction.followup.send("City added to bot database", ephemeral=False)
                    return
                else:
                    with self.bot_db.cursor() as cursor:
                        cursor.execute(query, (county_code, city))
                        cursor.fetchall()
                        cursor.close()
                    self.bot_db.commit()
                    await interaction.followup.send("City added to bot database", ephemeral=False)
                    return
            else:
                await interaction.followup.send("City added to bot database", ephemeral=False)
                return
        
        @self.bot.tree.command(name="removecity", description="Remove a city from the bot database")
        async def slash_removecity(interaction: discord.Interaction, city: str):
            """(Slash command) Removes a city from the bot's database."""
            query = "DELETE FROM server_counties WHERE city_name = %s"
            with self.bot_db.cursor() as cursor:
                cursor.execute(query, (city,))
                cursor.fetchall()
                cursor.close()
            self.bot_db.commit()
            await interaction.response.send_message(f"Deletion query for {city} executed successfully")
            return
        
        #Dev only
        @self.bot.tree.command(name="getwarnings", description="Get the active warnings for the provided area")
        async def slash_getwarnings(interaction: discord.Interaction, city: str = None, state: str = None, county_code: str = None):
            # Ensure at least one argument is provided
            if (city is None) and (state is None) and (county_code is None):
                await interaction.response.send_message("You must provide at least 1 argument", ephemeral=True)
                return
            
            # Check if user is authorized
            if interaction.user.id != int(os.getenv("DEVID_1")):
                await interaction.response.send_message("You are not authorized to run this command.", ephemeral=True)
                return
            
            # Acknowledge the interaction to avoid timeout
            await interaction.response.defer()

            # Fetch warnings based on input criteria
            alerts = None
            if city is not None:
                query = "SELECT nws_county_id FROM server_counties WHERE city_name = %s" 
                with self.bot_db.cursor() as cursor:
                    cursor.execute(query, (city,))
                    res = cursor.fetchone()
                if res is not None:
                    alerts = self.NWS.check_active_alerts(county_code=res[0])
                else:
                    await interaction.followup.send("City not found in database", ephemeral=True)
                    return
            elif state is not None:
                state_code = self.__parse_state_name(state)
                if state_code:
                    alerts = self.NWS.check_active_alerts(state_code=state_code)
                else:
                    await interaction.followup.send("State not found in database", ephemeral=True)
                    return
            elif county_code is not None:
                alerts = self.NWS.check_active_alerts(county_code=county_code)

            # Check if there are active alerts
            if not alerts or "features" not in alerts or len(alerts["features"]) == 0:
                await interaction.followup.send("No active warnings found.", ephemeral=True)
                return

            # Send each embed separately
            for feature in alerts["features"]:
                # Extract event details
                onset = feature["properties"]["onset"]
                expires = feature["properties"]["expires"]
                headline = feature["properties"]["headline"]
                description = feature["properties"]["description"]
                
                # Analyze alert details
                print(headline.lower())
                alert_type = re.search(r"(.+ watch|.+ warning)", headline.lower())
                print(alert_type)
                severity = None
                
                # Flags for special alerts
                pds_tor = False
                emergency_tor = False
                destructive_tstorm = False
                emergency_flood = False
                if alert_type is not None:
                    ## **Check Tornado Details (PDS, Emergency)**
                    if "tornado" in alert_type[0].lower():
                        if "particularly dangerous situation" in description.lower():
                            pds_tor = True
                        else:
                            nws_headline = feature["properties"]["parameters"].get("NWSheadline", [""])[0].lower()
                            severity = re.search(r"(warning|emergency)", nws_headline.lower())
                            if (severity is not None) and ("emergency" in severity[0].lower()):
                                emergency_tor = True

                    ## **Check Severe Thunderstorm Details (Destructive)**
                    elif "severe thunderstorm" in alert_type[0].lower():
                        damage_threat = feature["properties"]["parameters"].get("damageThreat", ["Unknown"])[0]
                        if damage_threat.lower() == "destructive":
                            destructive_tstorm = True

                    ## **Check Flash Flood Details (Emergency)**
                    elif "flash flood" in alert_type[0].lower():
                        nws_headline = feature["properties"]["parameters"].get("NWSheadline", [""])[0].lower()
                        if ("flash flood emergency" in nws_headline) or ("flash flood emergency" in description):
                            emergency_flood = True
                
                # Determine embed color based on alert type
                color = 0xff0019 # Default: red for svr alerts
                
                if pds_tor or emergency_tor:
                    color = 0x800080 # Purple for PDS Tor or Tor-E
                elif destructive_tstorm:
                    color = 0xffa500 # Orange for destructive tstorm
                elif emergency_flood:
                    color = 0x0000ff # Blue for flash flood emergency
                
                # ** Set custom embed titles for special alerts **
                embed_title = headline
                if emergency_tor:
                    embed_title = "🚨 TORNADO EMERGENCY 🚨"
                elif pds_tor:
                    embed_title = "🌪️ PDS TORNADO WARNING 🌪️"
                elif destructive_tstorm:
                    embed_title = "🌩️ DESTRUCTIVE SVR. THUNDERSTORM WARNING 🌩️"
                elif emergency_flood:
                    embed_title = "🌊 FLASH FLOOD EMERGENCY 🌊"

                # Create embed message
                embed = discord.Embed(title=embed_title, description=f"{description}", color=color)
                embed.add_field(name="EVENT DETAILS", value=f"🕒 ONSET: {onset}\n⏳ EXPIRES: {expires}")

                # Send each embed as a followup message
                await interaction.followup.send(embed=embed)

            return
                    
            
        #@self.bot.tree.command(name="monitorstate")
        #async def slash_monitorstate(interaction: discord.Interaction, state_code: str = None, state_name: str = None):
            
        #@self.bot.tree.command(name="monitorcounty")
        
    def run(self):
        """Run the bot given the token"""
        self.bot.run(os.getenv("BOT_TOKEN"))

if __name__ == "__main__":
    bot = Bot()
    bot.run()