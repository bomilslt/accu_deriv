#!/usr/bin/env python3
"""
Bot de Trading Accumulator pour Deriv
Stratégie: Scalping 4-5 ticks avec protection anti-volatilité
"""

import asyncio
import json
import logging
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import websocket
import os
from dotenv import load_dotenv

# Chargement des variables d'environnement
load_dotenv()

# Import configuration
try:
    import config
except ImportError:
    print("Erreur: Fichier config.py introuvable. Veuillez le créer.")
    sys.exit(1)

# Configuration des logs
def setup_logger(debug_mode: bool, log_file: str) -> logging.Logger:
    logger = logging.getLogger("AccuBot")
    logger.setLevel(logging.DEBUG if debug_mode else logging.INFO)
    
    # Support des émojis sur Windows
    if sys.platform == 'win32':
        import codecs
        sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer)

    # Formateur
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Handler Console
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.DEBUG if debug_mode else logging.INFO)
    logger.addHandler(console_handler)
    
    # Handler Fichier (toujours activé pour traçabilité VPS)
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)
    
    return logger

logger = setup_logger(config.DEBUG_MODE, config.LOG_FILE)

class AccumulatorBot:
    def __init__(self):
        self.app_id = int(os.getenv("DERIV_APP_ID", config.DERIV_APP_ID))
        self.token = os.getenv("DERIV_TOKEN", config.DERIV_TOKEN)
        self.symbol = config.SYMBOL
        self.stake = config.INITIAL_STAKE
        
        self.ws_url = f"wss://ws.binaryws.com/websockets/v3?app_id={self.app_id}"
        self.ws = None
        self.is_connected = False
        self.account_info = {}
        
        # Données de marché
        self.tick_history: List[float] = []
        self.current_price = 0.0
        self.entry_price = 0.0
        self.entry_time = 0
        self.contract_id = None
        self.purchase_price = 0.0
        self.selected_growth_rate = 0.0
        self.barrier_limit = 0.0
        
        # État du trading
        self.in_position = False
        self.running = True
        self.tick_count = 0
        
        # Statistiques
        self.total_trades = 0
        self.wins = 0
        self.losses = 0
        self.total_profit = 0.0

    async def connect(self):
        """Établissement de la connexion WebSocket"""
        logger.info(f"Connexion à Deriv API ({self.symbol})...")
        
        self.ws = websocket.WebSocket()
        try:
            self.ws.connect(self.ws_url)
            self.is_connected = True
            logger.info("Connecté avec succès!")
            
            # Authorisation
            await self.authorize()
            
            # Souscription aux ticks
            await self.subscribe_ticks()
            
        except Exception as e:
            logger.error(f"Échec de connexion: {e}")
            self.is_connected = False
            raise

    async def authorize(self):
        """Authentification avec le token"""
        if not self.token:
            logger.warning("Pas de token fourni. Mode observation uniquement.")
            return
            
        auth_msg = {"authorize": self.token}
        self.ws.send(json.dumps(auth_msg))
        response = json.loads(self.ws.recv())
        
        if "error" in response:
            logger.error(f"Erreur d'autorisation: {response['error']['message']}")
            raise Exception("Authorization failed")
        
        self.account_info = response.get("authorize", {})
        logger.info(f"Compte connecté: {self.account_info.get('loginid', 'N/A')}")
        logger.info(f"Solde: {self.account_info.get('balance', 0)} {self.account_info.get('currency', 'USD')}")

    async def subscribe_ticks(self):
        """Souscription au flux de ticks en temps réel"""
        sub_msg = {
            "ticks": self.symbol,
            "subscribe": 1
        }
        self.ws.send(json.dumps(sub_msg))
        logger.debug(f"Souscrit aux ticks pour {self.symbol}")

    def calculate_volatility(self) -> float:
        """Calcule la volatilité (écart-type) sur les N derniers ticks"""
        if len(self.tick_history) < config.VOLATILITY_PERIOD:
            return float('inf')  # Pas assez de données
        
        # Calcul des variations en pourcentage
        changes = []
        for i in range(1, len(self.tick_history)):
            pct_change = abs(self.tick_history[i] - self.tick_history[i-1]) / self.tick_history[i-1]
            changes.append(pct_change)
        
        # Écart-type
        mean = sum(changes) / len(changes)
        variance = sum((x - mean) ** 2 for x in changes) / len(changes)
        std_dev = variance ** 0.5
        
        return std_dev

    def select_best_rate(self, volatility: float) -> Optional[Tuple[float, float]]:
        """
        Sélectionne le meilleur taux de croissance selon la volatilité
        Retourne (taux, barrière) ou None si trop risqué
        """
        safety_threshold = volatility * config.VOLATILITY_MULTIPLIER
        
        logger.debug(f"Volatilité: {volatility:.6f}, Seuil sécurité: {safety_threshold:.6f}")
        
        # Trier les taux du plus élevé au plus bas
        sorted_rates = sorted(config.BARRIER_OPTIONS.items(), key=lambda x: x[0], reverse=True)
        
        for rate, barrier in sorted_rates:
            if barrier > safety_threshold:
                logger.info(f"Taux sélectionné: {rate*100:.1f}% (Barrière: {barrier*100:.5f}%)")
                return rate, barrier
        
        logger.warning("Aucun taux sûr disponible - Volatilité trop élevée")
        return None

    async def buy_accumulator(self, growth_rate: float, barrier: float):
        """Achat d'un contrat Accumulator"""
        if not self.token:
            logger.warning("Mode simulation: Achat ignoré (pas de token)")
            self.in_position = True
            self.entry_price = self.current_price
            self.selected_growth_rate = growth_rate
            self.barrier_limit = barrier
            self.entry_time = time.time()
            self.tick_count = 0
            self.purchase_price = self.stake
            return True
        
        buy_msg = {
            "buy": 1,
            "price": self.stake,
            "parameters": {
                "amount": self.stake,
                "basis": "stake",
                "contract_type": "ACCUMULATOR",
                "currency": "USD",
                "symbol": self.symbol,
                "duration": 85,  # Durée max (on sortira avant)
                "barrier": str(barrier),  # Barrière en décimal
                "growth_rate": str(growth_rate)
            }
        }
        
        self.ws.send(json.dumps(buy_msg))
        response = json.loads(self.ws.recv())
        
        if "error" in response:
            logger.error(f"Erreur d'achat: {response['error']['message']}")
            return False
        
        contract = response.get("buy", {})
        self.contract_id = contract.get("contract_id")
        self.purchase_price = contract.get("buy_price", self.stake)
        self.in_position = True
        self.entry_price = self.current_price
        self.selected_growth_rate = growth_rate
        self.barrier_limit = barrier
        self.entry_time = time.time()
        self.tick_count = 0
        
        logger.info(f"✅ CONTRAT ACHETÉ - ID: {self.contract_id}, Prix: ${self.purchase_price:.2f}")
        return True

    def check_knockout(self) -> bool:
        """Vérifie si le prix a touché la barrière (knock-out)"""
        if not self.in_position:
            return False
        
        price_change_pct = abs(self.current_price - self.entry_price) / self.entry_price
        
        if price_change_pct >= self.barrier_limit:
            logger.error(f"❌ KNOCK-OUT! Variation: {price_change_pct*100:.5f}% > Barrière: {self.barrier_limit*100:.5f}%")
            self.in_position = False
            self.losses += 1
            self.total_trades += 1
            self.total_profit -= self.purchase_price
            return True
        
        return False

    def check_abnormal_move(self) -> bool:
        """Option C: Détecte un mouvement anormal pour sortie anticipée"""
        if len(self.tick_history) < 3:
            return False
        
        # Variation du dernier tick
        last_change = abs(self.tick_history[-1] - self.tick_history[-2]) / self.tick_history[-2]
        
        # Volatilité récente
        recent_vol = self.calculate_volatility()
        
        if recent_vol == 0 or recent_vol == float('inf'):
            return False
        
        threshold = recent_vol * config.ABNORMAL_MOVE_THRESHOLD
        
        if last_change > threshold:
            logger.warning(f"⚠️ MOUVEMENT ANORMAL détecté: {last_change*100:.5f}% (Seuil: {threshold*100:.5f}%)")
            return True
        
        return False

    def calculate_current_profit(self) -> float:
        """Calcule le profit actuel basé sur le nombre de ticks"""
        if not self.in_position:
            return 0.0
        
        # Formule: Stake * (1 + growth_rate)^ticks
        current_value = self.purchase_price * ((1 + self.selected_growth_rate) ** self.tick_count)
        return current_value - self.purchase_price

    async def sell_contract(self, reason: str):
        """Vente anticipée du contrat (Take Profit)"""
        if not self.contract_id or not self.token:
            # Mode simulation
            profit = self.calculate_current_profit()
            logger.info(f"💰 SORTIE ({reason}): Profit simulé ${profit:.2f}")
            self.in_position = False
            self.wins += 1
            self.total_trades += 1
            self.total_profit += profit
            return
        
        sell_msg = {"sell": self.contract_id}
        self.ws.send(json.dumps(sell_msg))
        response = json.loads(self.ws.recv())
        
        if "error" in response:
            logger.error(f"Erreur de vente: {response['error']['message']}")
            return
        
        sell_result = response.get("sell", {})
        profit = sell_result.get("profit", 0)
        
        logger.info(f"💰 SORTIE ({reason}): Profit réel ${profit:.2f}")
        self.in_position = False
        self.wins += 1
        self.total_trades += 1
        self.total_profit += profit

    async def process_tick(self, tick_data: dict):
        """Traitement principal à chaque nouveau tick"""
        price = tick_data.get("quote", 0.0)
        if price == 0:
            return
        
        self.current_price = price
        self.tick_history.append(price)
        
        # Garder seulement les N derniers ticks
        if len(self.tick_history) > config.VOLATILITY_PERIOD + 5:
            self.tick_history.pop(0)
        
        # Si pas en position, essayer d'entrer
        if not self.in_position:
            volatility = self.calculate_volatility()
            selection = self.select_best_rate(volatility)
            
            if selection:
                growth_rate, barrier = selection
                await self.buy_accumulator(growth_rate, barrier)
        else:
            # En position - gérer le trade
            self.tick_count += 1
            
            # Vérifier Knock-out
            if self.check_knockout():
                return
            
            # Vérifier mouvement anormal (Option C)
            if self.check_abnormal_move() and self.tick_count >= 2:
                await self.sell_contract("Mouvement anormal")
                return
            
            # Vérifier Take Profit par nombre de ticks (Option B)
            if self.tick_count >= config.TARGET_TICKS_MAX:
                profit = self.calculate_current_profit()
                await self.sell_contract(f"TP {self.tick_count} ticks (${profit:.2f})")
                return
            
            # Log intermédiaire
            if config.DEBUG_MODE and self.tick_count % 2 == 0:
                profit = self.calculate_current_profit()
                logger.debug(f"Tick {self.tick_count}: Prix={price}, Profit=${profit:.2f}")

    def print_summary(self):
        """Affiche un résumé des performances"""
        logger.info("="*50)
        logger.info("RÉSUMÉ DES PERFORMANCES")
        logger.info("="*50)
        logger.info(f"Total Trades: {self.total_trades}")
        logger.info(f"Gagnants: {self.wins}")
        logger.info(f"Perdants: {self.losses}")
        logger.info(f"Win Rate: {(self.wins/self.total_trades*100) if self.total_trades > 0 else 0:.1f}%")
        logger.info(f"Profit Total: ${self.total_profit:.2f}")
        logger.info("="*50)

    async def run(self):
        """Boucle principale du bot"""
        logger.info("🚀 Démarrage du Bot Accumulator...")
        
        try:
            await self.connect()
            
            while self.running:
                # Attendre les données WebSocket
                if self.ws.connected:
                    message = self.ws.recv()
                    data = json.loads(message)
                    
                    # Gérer les différents types de messages
                    if "tick" in data:
                        await self.process_tick(data["tick"])
                    elif "error" in data:
                        logger.error(f"Erreur API: {data['error']['message']}")
                
                await asyncio.sleep(0.1)  # Petit délai pour éviter CPU spike
                
        except KeyboardInterrupt:
            logger.info("\n⛔ Arrêt manuel demandé...")
            self.running = False
        except Exception as e:
            logger.error(f"Erreur critique: {e}", exc_info=True)
            self.running = False
        finally:
            if self.in_position:
                logger.warning("Position encore ouverte, tentative de fermeture...")
                await self.sell_contract("Fermeture bot")
            
            self.print_summary()
            logger.info("Bot arrêté.")

if __name__ == "__main__":
    # Vérification préliminaire
    if not config.BARRIER_OPTIONS:
        print("Erreur: Aucune barrière configurée dans config.py")
        sys.exit(1)
    
    print(f"Mode DEBUG: {config.DEBUG_MODE}")
    print(f"Symbole: {config.SYMBOL}")
    print(f"Mise: ${config.INITIAL_STAKE}")
    print(f"Objectif ticks: {config.TARGET_TICKS_MIN}-{config.TARGET_TICKS_MAX}")
    print("-" * 40)
    
    bot = AccumulatorBot()
    
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        print("\nArrêt propre effectué.")
