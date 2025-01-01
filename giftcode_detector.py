import discord
from discord.ext import commands
import re
import sqlite3
import hashlib
import json
import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
from datetime import datetime

# Définition des URLs et de la clé secrète
wos_player_info_url = "https://wos-giftcode-api.centurygame.com/api/player"
wos_giftcode_url = "https://wos-giftcode-api.centurygame.com/api/gift_code"
wos_giftcode_redemption_url = "https://wos-giftcode.centurygame.com"
wos_encrypt_key = "tB87#kPtkxqOS2"

retry_config = Retry(
    total=5,
    backoff_factor=1,
    status_forcelist=[429],
    allowed_methods=["POST"]
)

class GiftCodeDetector(commands.Cog):
    def __init__(self, bot, conn):
        self.bot = bot
        self.conn = conn
        self.c = conn.cursor()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Vérifier si le message provient d'un canal spécifique
        watched_channels = [1168982611092307988, 1322530169830899752]  # Remplacez par les bons IDs de canaux
        if message.channel.id in watched_channels:
            # Chercher un code cadeau dans le message
            match = re.search(r"Code:\s([A-Za-z0-9]+)", message.content)
            if match:
                giftcode = match.group(1)
                print(f"Code cadeau détecté: {giftcode}")
                
                # Répondre au message pour informer que le code a été détecté
                await message.channel.send(f"Code cadeau détecté: {giftcode}")
                
                # Ajouter le code aux utilisateurs de la base de données
                await self.add_code_to_users(giftcode, message.channel)

    async def add_code_to_users(self, giftcode: str, channel: discord.TextChannel):
        try:
            self.c.execute("SELECT * FROM users")
            users = self.c.fetchall()

            success_results = []
            no_change_results = []
            
            if not users:
                await channel.send("Aucun utilisateur trouvé dans la base de données.")
                return

            for user in users:
                fid = user[0]
                # Vérifier si le code a déjà été utilisé par cet utilisateur
                self.c.execute("SELECT 1 FROM user_giftcodes WHERE fid = ? AND giftcode = ?", (fid, giftcode))
                if not self.c.fetchone():
                    _, response_status = self.claim_giftcode_rewards_wos(player_id=fid, giftcode=giftcode)
                    if response_status == "SUCCESS":
                        # Ajouter le code comme utilisé pour cet utilisateur
                        self.c.execute("INSERT INTO user_giftcodes (fid, giftcode, status) VALUES (?, ?, ?)", (fid, giftcode, 'used'))
                        success_results.append(f"Code ajouté à {user[1]} (FID: {fid})")
                    else:
                        no_change_results.append(f"Échec de l'ajout pour {user[1]} (FID: {fid})")
                else:
                    no_change_results.append(f"{user[1]} (FID: {fid}) a déjà utilisé ce code")

            self.conn.commit()

            # Répondre avec les résultats
            if success_results:
                await channel.send("Le code cadeau a été ajouté aux joueurs suivants:\n" + "\n".join(success_results))
            if no_change_results:
                await channel.send("Les joueurs suivants n'ont pas reçu le code cadeau (déjà utilisé ou erreur):\n" + "\n".join(no_change_results))
            if not success_results and not no_change_results:
                await channel.send("Aucun utilisateur n'a été affecté par ce code cadeau.")
        except sqlite3.Error as e:
            print(f"Erreur de base de données : {e}")
            await channel.send(f"Une erreur est survenue avec la base de données : {e}")

    def encode_data(self, data):
        secret = wos_encrypt_key
        sorted_keys = sorted(data.keys())
        encoded_data = "&".join(
            [
                f"{key}={json.dumps(data[key]) if isinstance(data[key], dict) else data[key]}"
                for key in sorted_keys
            ]
        )
        sign = hashlib.md5(f"{encoded_data}{secret}".encode()).hexdigest()
        return {"sign": sign, **data}

    def get_stove_info_wos(self, player_id):
        session = requests.Session()
        session.mount("https://", HTTPAdapter(max_retries=retry_config))

        headers = {
            "accept": "application/json, text/plain, */*",
            "content-type": "application/x-www-form-urlencoded",
            "origin": wos_giftcode_redemption_url,
        }

        data_to_encode = {
            "fid": f"{player_id}",
            "time": f"{int(datetime.now().timestamp())}",
        }
        data = self.encode_data(data_to_encode)

        response_stove_info = session.post(
            wos_player_info_url,
            headers=headers,
            data=data,
        )
        return session, response_stove_info

    def claim_giftcode_rewards_wos(self, player_id, giftcode):
        session, response_stove_info = self.get_stove_info_wos(player_id=player_id)
        if response_stove_info.json().get("msg") == "success":
            data_to_encode = {
                "fid": f"{player_id}",
                "cdk": giftcode,
                "time": f"{int(datetime.now().timestamp())}",
            }
            data = self.encode_data(data_to_encode)

            response_giftcode = session.post(
                wos_giftcode_url,
                data=data,
            )
            
            response_json = response_giftcode.json()
            print(f"Response for {player_id}: {response_json}")
            
            if response_json.get("msg") == "SUCCESS":
                return session, "SUCCESS"
            elif response_json.get("msg") == "RECEIVED." and response_json.get("err_code") == 40008:
                return session, "ALREADY_RECEIVED"
            elif response_json.get("msg") == "CDK NOT FOUND." and response_json.get("err_code") == 40014:
                return session, "CDK_NOT_FOUND"
            elif response_json.get("msg") == "SAME TYPE EXCHANGE." and response_json.get("err_code") == 40011:
                return session, "ALREADY_RECEIVED"
            else:
                return session, "ERROR"


async def setup(bot):
    # Ajoutez le cog au bot et passez la connexion à la base de données
    await bot.add_cog(GiftCodeDetector(bot, bot.conn))
