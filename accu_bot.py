#!/usr/bin/env python3
"""
Bot de Trading Accumulator pour Deriv (nouvelle API 2026)
Stratégie: Scalping 4-5 ticks avec protection anti-volatilité

Authentification nouvelle API:
  - Token PAT (format pat_...) envoyé en header Authorization: Bearer
  - App ID (chaîne alphanumérique) envoyé en header Deriv-App-ID
  - L'URL du WebSocket de trading est obtenue via REST (endpoint OTP)
  - Les messages WebSocket (ticks, buy, sell, ping) gardent l'ancien format
"""

import asyncio
import atexit
import json
import logging
import sys
import time
import urllib.error
import urllib.request
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

# Endpoints de la nouvelle API Deriv
REST_BASE = "https://api.derivws.com/trading/v1/options"
PUBLIC_WS = "wss://api.derivws.com/trading/v1/options/ws/public"


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

LOCK_FILE = "bot.lock"


def _pid_alive(pid: str) -> bool:
    """Vérifie si un processus est encore vivant (Windows + Unix)."""
    try:
        p = int(pid)
    except (TypeError, ValueError):
        return False
    if p <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        SYNCHRONIZE = 0x00100000
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, p)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(p, 0)
        return True
    except OSError:
        return False


def acquire_lock() -> bool:
    """Verrou fichier pour empêcher deux instances simultanées
    (deux bots sur le même compte se bloquent mutuellement:
    limite de positions ACCU ouvertes très basse)."""
    try:
        if os.path.exists(LOCK_FILE):
            with open(LOCK_FILE) as f:
                old_pid = f.read().strip()
            if old_pid and _pid_alive(old_pid):
                return False  # Une instance vivante tourne déjà
        with open(LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))
        atexit.register(lambda: os.path.exists(LOCK_FILE) and os.remove(LOCK_FILE))
        return True
    except OSError:
        return True  # Verrou impossible (permissions): on continue sans


class AccumulatorBot:
    def __init__(self):
        self.app_id = str(os.getenv("DERIV_APP_ID", "") or config.DERIV_APP_ID)
        self.token = os.getenv("DERIV_TOKEN", config.DERIV_TOKEN)
        # Overrides possibles via .env: DERIV_SYMBOL, DERIV_STAKE, DERIV_ACCOUNT_TYPE
        self.symbol = os.getenv("DERIV_SYMBOL", config.SYMBOL)
        self.stake = float(os.getenv("DERIV_STAKE") or config.INITIAL_STAKE)
        self.account_type = str(os.getenv("DERIV_ACCOUNT_TYPE", config.ACCOUNT_TYPE)).lower()

        self.ws = None
        self.is_connected = False
        self.account_id = None
        self.currency = "USD"
        self.observation_mode = False
        self._awaiting_order = False

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
        self.cooldown_ticks = 0  # Ticks à attendre après une clôture avant de ré-entrer

        # Statistiques
        self.total_trades = 0
        self.wins = 0
        self.losses = 0
        self.total_profit = 0.0
        self.consecutive_buy_errors = 0
        self._last_no_rate_warning = 0.0

        # Filtre de tendance (compter/limiter l'attente d'un signal)
        self._trend_wait_ticks = 0          # Ticks passés à chercher un signal sans succès
        self._last_no_trend_warning = 0.0   # Anti-log-spam: rappel WARNING toutes les 60 s

        # Confirmation de calme après une clôture
        self._fresh_ticks = 0   # Ticks FRAIS collectés depuis la dernière clôture
        self._calm_streak = 0   # Ticks consécutifs "calmes" (un tick brutal remet à 0)

    # ------------------------------------------------------------------ #
    # Couche REST (nouvelle API)                                         #
    # ------------------------------------------------------------------ #

    def _rest(self, method: str, path: str, body: Optional[dict] = None) -> Tuple[int, dict]:
        """Appel REST authentifié (Bearer PAT + Deriv-App-ID)."""
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(REST_BASE + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "Deriv-App-ID": self.app_id,
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")
            try:
                return e.code, json.loads(raw)
            except json.JSONDecodeError:
                return e.code, {"message": raw.strip()[:300]}

    def _rest_error(self, status: int, payload: dict) -> str:
        """Traduit une erreur REST en message actionnable."""
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            msg = str(errors[0].get("message", ""))
        else:
            msg = str(payload.get("message") or payload)[:200]
        hints = {
            "Invalid token format": (
                "le token ne commence pas par 'pat_' ou est tronqué. "
                "Régénérez-le et copiez-le ENTIÈREMENT dès sa création "
                "(il n'est affiché qu'une seule fois)."
            ),
            "Invalid or expired token": (
                "token refusé ou expiré. Régénérez-en un (droits lecture + trading) "
                "et recollez-le intégralement."
            ),
            "Deriv-App-ID header is required for PAT tokens": (
                "DERIV_APP_ID est manquant dans le .env."
            ),
        }
        hint = hints.get(msg, "")
        if status == 403:
            hint += " (vérifiez que le token a bien les droits/scopes requis)"
        return f"HTTP {status}: {msg}" + (f" — {hint}" if hint else "")

    # ------------------------------------------------------------------ #
    # Comptes + OTP                                                      #
    # ------------------------------------------------------------------ #

    def _extract_accounts(self, payload: dict) -> List[dict]:
        """Extrait la liste des comptes, robuste aux variations de schéma."""
        data = payload.get("data", payload)
        if isinstance(data, dict):
            accounts = None
            for key in ("accounts", "items", "results"):
                if isinstance(data.get(key), (list, dict)):
                    accounts = data[key]
                    break
            if accounts is None:
                accounts = [data]
        elif isinstance(data, list):
            accounts = data
        else:
            accounts = []
        if isinstance(accounts, dict):
            accounts = list(accounts.values())
        return [a for a in accounts if isinstance(a, dict)]

    @staticmethod
    def _account_id(account: dict) -> Optional[str]:
        return account.get("account_id") or account.get("id") or account.get("loginid")

    @staticmethod
    def _is_demo(account: dict) -> Optional[bool]:
        """Détecte si un compte est un compte démo (best-effort)."""
        for key in ("is_demo", "demo", "is_virtual"):
            if key in account:
                return bool(account[key])
        acc_type = str(account.get("account_type") or account.get("type") or "").lower()
        if acc_type:
            return "demo" in acc_type or "virtual" in acc_type
        loginid = str(account.get("loginid") or "")
        if loginid:
            return loginid.startswith("VRT")
        return None

    def select_account(self, accounts: List[dict]) -> dict:
        """Choisit le compte selon le type demandé ('demo' par défaut)."""
        want_demo = self.account_type == "demo"
        matching = [a for a in accounts if self._is_demo(a) == want_demo]
        if matching:
            account = matching[0]
            kind = "démo" if want_demo else "réel"
        else:
            account = accounts[0]
            kind = f"type inconnu (type demandé '{self.account_type}' non trouvé)"
            logger.warning(f"⚠️ Aucun compte {'démo' if want_demo else 'réel'} trouvé, "
                           f"utilisation du premier compte disponible")
        logger.info(f"Compte sélectionné ({kind}): {self._account_id(account)} "
                    f"({account.get('currency', 'USD')})")
        return account

    # ------------------------------------------------------------------ #
    # Connexion                                                          #
    # ------------------------------------------------------------------ #

    def _validate_credentials(self):
        """Validation préalable avec messages clairs (échec rapide)."""
        if not self.app_id or not str(self.app_id).strip():
            raise RuntimeError("DERIV_APP_ID manquant: renseignez l'ID (chaîne alphanumérique) "
                               "de votre app créée sur developers.deriv.com dans le .env")
        if str(self.app_id).strip().isdigit():
            logger.warning("⚠️ DERIV_APP_ID est purement numérique: c'est un ancien format. "
                           "L'ID d'une app créée sur la NOUVELLE interface est alphanumérique.")
        if not self.token:
            return  # Mode observation, géré par connect()
        token = str(self.token)
        if not token.startswith("pat_") or len(token) < 20:
            raise RuntimeError(
                f"DERIV_TOKEN invalide: il doit commencer par 'pat_' et faire plus de 20 caractères "
                f"(valeur actuelle: {len(token)} caractères, préfixe "
                f"'{token[:4]}...'). Le token est affiché UNE SEULE fois à sa création sur "
                "Deriv — régénérez-le et copiez-le intégralement, sans le tronquer."
            )

    async def connect(self):
        """Connexion: REST (comptes + OTP) puis WebSocket de trading."""
        self._validate_credentials()

        if not self.token:
            logger.warning("Pas de token fourni. Mode observation uniquement "
                           "(WebSocket public, trades simulés).")
            self.observation_mode = True
            self.ws = websocket.WebSocket()
            self.ws.connect(PUBLIC_WS, timeout=10)
            self.ws.settimeout(30)
            self.is_connected = True
            logger.info("Connecté au WebSocket public.")
            await self.subscribe_ticks()
            return

        logger.info(f"Connexion à la nouvelle API Deriv ({self.symbol})...")

        # 1) Liste des comptes
        status, payload = self._rest("GET", "/accounts")
        if status != 200:
            raise RuntimeError(f"Échec /accounts: {self._rest_error(status, payload)}")
        accounts = self._extract_accounts(payload)
        logger.debug(f"Réponse /accounts: {json.dumps(payload)[:500]}")
        if not accounts:
            raise RuntimeError(f"Aucun compte retourné par l'API: {json.dumps(payload)[:300]}")

        # 2) Choix du compte + OTP
        account = self.select_account(accounts)
        self.account_id = self._account_id(account)
        self.currency = account.get("currency") or "USD"

        status, payload = self._rest("POST", f"/accounts/{self.account_id}/otp", body={})
        if status != 200:
            raise RuntimeError(f"Échec OTP: {self._rest_error(status, payload)}")
        ws_url = (payload.get("data") or {}).get("url")
        if not ws_url:
            raise RuntimeError(f"Réponse OTP inattendue (pas de data.url): {json.dumps(payload)[:300]}")

        # 3) WebSocket authentifié par l'OTP (valable 120 s, usage unique)
        self.ws = websocket.WebSocket()
        self.ws.connect(ws_url, timeout=10)
        self.ws.settimeout(30)
        self.is_connected = True
        logger.info(f"Connecté avec succès! (compte {self.account_id}, {self.currency})")

        await self._fetch_balance()
        await self.subscribe_ticks()

    async def _fetch_balance(self):
        """Récupère le solde si l'API le permet (non bloquant en cas d'échec)."""
        try:
            data = self._send_and_await({"balance": 1}, expected="balance", timeout_s=8)
            if "error" in data:
                logger.debug(f"Solde indisponible: {data['error'].get('message')}")
            else:
                bal = data.get("balance", {})
                logger.info(f"Solde: {bal.get('balance', 'N/A')} {bal.get('currency', self.currency)}")
        except Exception as e:
            logger.debug(f"Solde non récupéré: {e}")

    async def subscribe_ticks(self):
        """Souscription au flux de ticks en temps réel"""
        sub_msg = {
            "ticks": self.symbol,
            "subscribe": 1
        }
        self.ws.send(json.dumps(sub_msg))
        logger.debug(f"Souscrit aux ticks pour {self.symbol}")

    # ------------------------------------------------------------------ #
    # Envoi/réception ordres                                             #
    # ------------------------------------------------------------------ #

    def _send_and_await(self, request: dict, expected: str, timeout_s: float = 15.0) -> dict:
        """Envoie une requête et lit les messages jusqu'à la réponse attendue.
        Les ticks reçus pendant l'attente sont ignorés (pas de re-entrées)."""
        self._awaiting_order = True
        self.ws.send(json.dumps(request))
        self.ws.settimeout(timeout_s)
        try:
            while True:
                data = json.loads(self.ws.recv())
                if expected in data or "error" in data:
                    return data
        finally:
            self.ws.settimeout(30)
            self._awaiting_order = False

    def _open_contracts(self) -> List[dict]:
        """Positions actuellement ouvertes (via l'appel portfolio)."""
        try:
            data = self._send_and_await({"portfolio": 1}, expected="portfolio", timeout_s=10)
            if "error" in data:
                logger.debug(f"Portfolio indisponible: {data['error'].get('message')}")
                return []
            return data.get("portfolio", {}).get("contracts", [])
        except Exception as e:
            logger.debug(f"Portfolio non récupéré: {e}")
            return []

    async def _sell_by_id(self, contract_id, buy_price=None):
        """Vend un contrat précis (nettoyage de positions orphelines).
        Retourne le profit si connu, sinon None."""
        if not contract_id:
            return None
        for attempt in range(2):
            try:
                response = self._send_and_await({"sell": contract_id, "price": 0},
                                                expected="sell")
            except websocket.WebSocketTimeoutException:
                response = {"error": {"message": "timeout"}}
            if "error" not in response:
                break
            if attempt == 0:
                await asyncio.sleep(3)  # L'entry spot tick n'est peut-être pas arrivé
        if "error" in response:
            logger.error(f"Impossible de vendre le contrat {contract_id}: "
                         f"{response['error'].get('message')}")
            return None
        sold = response.get("sell", {}).get("sold_for", 0)
        profit = (sold - buy_price) if (sold and buy_price is not None) else None
        extra = f" (profit ${profit:.2f})" if profit is not None else ""
        logger.info(f"🧹 Nettoyage: contrat {contract_id} vendu pour ${sold}{extra}")
        return profit

    def _mark_closed(self, profit: float):
        """Clôture commune: stats + cooldown avant la prochaine entrée."""
        self.in_position = False
        self.contract_id = None
        self.wins += 1
        self.total_trades += 1
        self.total_profit += profit
        self.cooldown_ticks = config.COOLDOWN_TICKS
        self._trend_wait_ticks = 0  # Après une clôture, re-exiger un signal clair
        self._fresh_ticks = 0  # Il faut de la donnée fraîche avant de ré-évaluer
        self._calm_streak = 0  # ... et un calme soutenu reconfirmé

    async def buy_accumulator(self, growth_rate: float, barrier: float):
        """Achat d'un contrat Accumulator (contract_type ACCU sur la nouvelle API)"""
        if self.observation_mode:
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
                "contract_type": "ACCU",
                "currency": self.currency,
                "underlying_symbol": self.symbol,
                "growth_rate": growth_rate
            }
        }

        # Note: sur la nouvelle API, l'ACCU n'a ni durée ni barrière explicite:
        # le growth_rate détermine la barrière (±X%), et le contrat court sans
        # échéance jusqu'au knock-out ou à la vente.
        try:
            response = self._send_and_await(buy_msg, expected="buy")
        except websocket.WebSocketTimeoutException:
            # L'ordre a pu être exécuté sans que la réponse arrive: on vérifie
            # l'état réel du portefeuille au lieu de deviner.
            logger.error("Timeout sur la réponse d'achat — vérification du portefeuille...")
            accu = [c for c in self._open_contracts() if c.get("contract_type") == "ACCU"]
            if accu:
                c = accu[0]
                self.contract_id = c.get("contract_id")
                self.purchase_price = c.get("buy_price", self.stake)
                self.in_position = True
                self.entry_price = self.current_price
                self.selected_growth_rate = growth_rate
                self.barrier_limit = barrier
                self.entry_time = time.time()
                self.tick_count = 0
                logger.warning(f"Achat probablement exécuté malgré le timeout: "
                               f"contrat {self.contract_id} adopté")
                return True
            logger.error("Achat non confirmé et aucune position ouverte trouvée")
            return False

        if "error" in response:
            err_msg = str(response["error"].get("message", ""))
            logger.error(f"Erreur d'achat: {err_msg}")
            if "too many open positions" in err_msg.lower():
                # Des contrats restés ouverts bloquent les achats. On nettoie
                # via le PORTFEUILLE (vérité serveur), pas via notre état local
                # qui peut être faux (ex: faux knock-out ayant mis contract_id à None).
                logger.warning("Position(s) bloquante(s) — nettoyage du portefeuille")
                for c in self._open_contracts():
                    if c.get("contract_type") == "ACCU":
                        await self._sell_by_id(c.get("contract_id"), c.get("buy_price"))
                self.in_position = False
                self.contract_id = None
                self.cooldown_ticks = config.COOLDOWN_TICKS
                self._trend_wait_ticks = 0
                self._fresh_ticks = 0
                self._calm_streak = 0
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

        logger.info(f"✅ CONTRAT ACHETÉ - ID: {self.contract_id}, "
                    f"Taux: {growth_rate*100:.0f}%, Prix: ${self.purchase_price:.2f}")
        return True

    async def sell_contract(self, reason: str):
        """Vente anticipée du contrat (Take Profit / sécurité)"""
        if not self.contract_id or self.observation_mode:
            # Mode simulation
            profit = self.calculate_current_profit()
            logger.info(f"💰 SORTIE ({reason}): Profit simulé ${profit:.2f}")
            self.in_position = False
            self.wins += 1
            self.total_trades += 1
            self.total_profit += profit
            self.cooldown_ticks = config.COOLDOWN_TICKS
            self._trend_wait_ticks = 0
            self._fresh_ticks = 0
            self._calm_streak = 0
            return

        # price: 0 = vente au marché (champ obligatoire sur la nouvelle API)
        sell_msg = {"sell": self.contract_id, "price": 0}
        try:
            response = self._send_and_await(sell_msg, expected="sell")
        except websocket.WebSocketTimeoutException:
            # La vente a pu être exécutée sans réponse: on vérifie l'état réel.
            logger.error("Timeout sur la réponse de vente — vérification du portefeuille...")
            still_open = any(c.get("contract_id") == self.contract_id
                             for c in self._open_contracts())
            if still_open:
                logger.error(f"Vente non confirmée: contrat {self.contract_id} toujours ouvert — "
                             "il sera revendu à la prochaine tentative d'achat ou à l'arrêt")
                return
            profit = self.calculate_current_profit()
            logger.warning(f"💰 SORTIE ({reason}): contrat vraisemblablement vendu "
                           f"(timeout), profit estimé ${profit:.2f}")
            self._mark_closed(profit)
            return

        if "error" in response:
            # Si le contrat vient d'être acheté, l'entry spot tick n'est peut-être
            # pas encore arrivé: on retente une fois après quelques secondes.
            logger.warning(f"Erreur de vente: {response['error']['message']} — nouvelle tentative dans 3s")
            await asyncio.sleep(3)
            response = self._send_and_await(sell_msg, expected="sell")
            if "error" in response:
                logger.error(f"Erreur de vente définitive (contrat {self.contract_id} reste ouvert): "
                             f"{response['error']['message']}")
                return

        sell_result = response.get("sell", {})
        sold_price = sell_result.get("sold_for", 0)
        profit = sold_price - self.purchase_price if sold_price else 0

        logger.info(f"💰 SORTIE ({reason}): Profit réel ${profit:.2f}")
        self._mark_closed(profit)

    # ------------------------------------------------------------------ #
    # Stratégie (inchangée)                                              #
    # ------------------------------------------------------------------ #

    def calculate_volatility(self) -> float:
        """Calcule la volatilité (écart-type des variations tick-à-tick)
        sur exactement les VOLATILITY_PERIOD derniers ticks."""
        n = config.VOLATILITY_PERIOD
        if len(self.tick_history) < n + 1:
            return float('inf')  # Pas assez de données fraîches

        # Fenêtre exacte: n+1 prix -> n variations tick-à-tick
        window = self.tick_history[-(n + 1):]
        changes = [abs(window[i] - window[i - 1]) / window[i - 1]
                   for i in range(1, len(window)) if window[i - 1] != 0]

        # Écart-type
        mean = sum(changes) / len(changes)
        variance = sum((x - mean) ** 2 for x in changes) / len(changes)
        return variance ** 0.5

    def calculate_trend_signal(self) -> Optional[str]:
        """
        Filtre de tendance: détecte si le marché "marche régulièrement" dans
        une direction sur les TREND_WINDOW derniers ticks (micro-tendance).
        Retourne 'up' / 'down' si la majorité des ticks vont dans le même sens,
        None si le marché oscille en va-et-vient (chaotique) ou si les données
        sont insuffisantes.

        Un ACCU gagne quand le prix fait de petits pas réguliers qui restent
        dans la barrière — pas quand il fait des allers-retours. On ne rentre
        donc qu'en présence d'une tendance directionnelle claire.
        """
        n = config.TREND_WINDOW
        if n < 1:
            return None
        if len(self.tick_history) < n + 1:
            return None  # Pas assez de données fraîches

        # Fenêtre exacte: n+1 prix -> n variations tick-à-tick
        window = self.tick_history[-(n + 1):]
        up_count = sum(1 for i in range(1, len(window)) if window[i] > window[i - 1])
        ratio = up_count / n

        if ratio >= config.TREND_DIRECTIONALITY:
            return 'up'
        if ratio <= 1 - config.TREND_DIRECTIONALITY:
            return 'down'
        return None

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
                logger.debug(f"Taux sélectionné: {rate*100:.1f}% (Barrière: {barrier*100:.5f}%)")
                return rate, barrier

        # Message par-tick -> DEBUG; rappel WARNING au plus toutes les 60 s
        logger.debug("Aucun taux sûr - Volatilité trop élevée")
        now = time.time()
        if now - self._last_no_rate_warning > 60:
            logger.warning("Volatilité trop élevée: aucun taux sûr disponible "
                           "(rappel au plus toutes les 60 s)")
            self._last_no_rate_warning = now
        return None

    async def check_knockout(self) -> bool:
        """Vérifie si le dernier tick a touché la barrière (knock-out).
        La barrière ACCU s'applique tick par tick (variation vs le tick
        PRÉCÉDENT), mais seulement À PARTIR du second tick après l'achat:
        le premier tick est l'entry spot tick, il ne peut pas knockouter
        le contrat côté serveur."""
        if not self.in_position or len(self.tick_history) < 2:
            return False
        if self.tick_count < 2:
            return False  # Entry spot tick: pas encore knockoutable

        last, prev = self.tick_history[-1], self.tick_history[-2]
        if prev == 0:
            return False

        tick_change = abs(last - prev) / prev

        if tick_change >= self.barrier_limit:
            # En mode réel, on confirme l'état côté serveur avant de déclarer
            # une perte: si le contrat est encore vivant (délai de propagation
            # ou sémantique légèrement décalée), on sort défensivement au lieu
            # de créer une perte fantôme + un contrat orphelin.
            if not self.observation_mode and self.contract_id:
                still_open = any(c.get("contract_id") == self.contract_id
                                 for c in self._open_contracts())
                if still_open:
                    logger.warning(f"⚠️ Tick hors barrière ({tick_change*100:.5f}%) mais "
                                   "contrat toujours vivant côté serveur — vente défensive")
                    await self.sell_contract("Tick hors barrière (vente défensive)")
                    return True

            logger.error(f"❌ KNOCK-OUT! Variation du tick: {tick_change*100:.5f}% "
                         f"> Barrière: {self.barrier_limit*100:.5f}%")
            self.in_position = False
            self.contract_id = None  # Le contrat s'est soldé tout seul (perte)
            self.losses += 1
            self.total_trades += 1
            self.total_profit -= self.purchase_price
            self.cooldown_ticks = config.COOLDOWN_TICKS
            self._trend_wait_ticks = 0  # Après un knock-out, re-exiger un signal
            self._fresh_ticks = 0
            self._calm_streak = 0
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
            # Détail en DEBUG: la raison figure déjà dans la ligne SORTIE (INFO)
            logger.debug(f"Mouvement anormal: {last_change*100:.5f}% (Seuil: {threshold*100:.5f}%)")
            return True

        return False

    def calculate_current_profit(self) -> float:
        """Calcule le profit actuel basé sur le nombre de ticks"""
        if not self.in_position:
            return 0.0

        # Formule: Stake * (1 + growth_rate)^ticks
        current_value = self.purchase_price * ((1 + self.selected_growth_rate) ** self.tick_count)
        return current_value - self.purchase_price

    def _check_calm(self) -> bool:
        """
        Le tick courant est-il "calme" ? Deux conditions:
          1) le dernier mouvement tick-à-tick reste sous CALM_MAX_TICK_MOVE
             (par défaut: la barrière la plus large des taux -> aucun à-coup brutal),
          2) un taux sûr existe avec la volatilité actuelle (fenêtre fraîche).
        Un tick non calme remet le compteur de confirmation à zéro (on ne se
        relance pas juste après un à-coup du marché).
        """
        if len(self.tick_history) < 2:
            return False
        prev = self.tick_history[-2]
        if prev == 0:
            return False
        move = abs(self.tick_history[-1] - prev) / prev
        calm_max = config.CALM_MAX_TICK_MOVE or max(config.BARRIER_OPTIONS.values())
        if move > calm_max:
            return False
        volatility = self.calculate_volatility()
        return self.select_best_rate(volatility) is not None

    async def process_tick(self, tick_data: dict):
        """Traitement principal à chaque nouveau tick"""
        if self._awaiting_order:
            return  # Ordre en cours: ignorer les ticks pour éviter les re-entrées

        price = tick_data.get("quote", 0.0)
        if price == 0:
            return

        self.current_price = price
        self.tick_history.append(price)

        # Garder seulement les N derniers ticks (assez pour la fenêtre la plus large)
        history_cap = max(config.VOLATILITY_PERIOD, config.REOBSERVE_TICKS,
                          config.TREND_WINDOW) + 5
        if len(self.tick_history) > history_cap:
            self.tick_history.pop(0)

        # Si pas en position, essayer d'entrer
        if not self.in_position:
            self._fresh_ticks += 1  # Données fraîches récoltées depuis la dernière clôture

            # Cooldown après une clôture: attendre des données fraîches
            if self.cooldown_ticks > 0:
                self.cooldown_ticks -= 1
                logger.debug(f"Cooldown: {self.cooldown_ticks} tick(s) restant(s)")
                return

            # Ré-observation: il faut assez de ticks FRAIS pour juger le niveau de
            # calme (évite de se relancer "à l'instant T" avec des données périmées).
            if self._fresh_ticks < config.REOBSERVE_TICKS:
                logger.debug(f"Ré-observation du marché: {self._fresh_ticks}"
                             f"/{config.REOBSERVE_TICKS} ticks frais")
                return

            # Confirmation d'un calme SOUTENU dans le temps: un tick trop brutal
            # remet le compteur à zéro (le marché n'est pas encore stable).
            calm = self._check_calm()
            self._calm_streak = (self._calm_streak + 1) if calm else 0
            if calm:
                logger.debug(f"Tick calme ({self._calm_streak}"
                             f"/{config.CALM_CONFIRM_TICKS})")
            else:
                logger.debug(f"Tick non calme — compteur remis à zéro "
                             f"({self._calm_streak}/{config.CALM_CONFIRM_TICKS})")
            if self._calm_streak < config.CALM_CONFIRM_TICKS:
                return

            # Filtre de tendance: on n'entre qu'en présence d'une micro-tendance
            # directionnelle, pour ne pas se relancer dans un marché qui oscille.
            if config.TREND_FILTER_ENABLED:
                trend = self.calculate_trend_signal()
                if trend is None:
                    self._trend_wait_ticks += 1
                    max_wait = config.TREND_MAX_WAIT_TICKS
                    now = time.time()
                    if 0 < max_wait < self._trend_wait_ticks:
                        # Fallback: entrée dès qu'un taux est sûr (un seul rappel min)
                        if now - self._last_no_trend_warning > 60:
                            logger.warning(f"⏳ Aucun signal de tendance pendant "
                                           f"{self._trend_wait_ticks} ticks — fallback: entrée "
                                           "dès qu'un taux est sûr (TREND_MAX_WAIT_TICKS dépassé)")
                            self._last_no_trend_warning = now
                    else:
                        # Pas de tendance: on continue d'étudier le marché au lieu d'acheter
                        logger.debug("Pas de tendance claire — on attend un signal "
                                     f"(wait {self._trend_wait_ticks})")
                        if now - self._last_no_trend_warning > 60:
                            logger.warning("Marché sans tendance claire: aucune entrée tant "
                                           "qu'il n'y a pas de mouvement régulier "
                                           "(rappel au plus toutes les 60 s)")
                            self._last_no_trend_warning = now
                        return
                else:
                    self._trend_wait_ticks = 0
                    logger.debug(f"Tendance détectée: {trend} — vérification volatilité")

            volatility = self.calculate_volatility()
            selection = self.select_best_rate(volatility)

            if selection:
                growth_rate, barrier = selection
                bought = await self.buy_accumulator(growth_rate, barrier)
                if bought:
                    self.consecutive_buy_errors = 0
                    self._trend_wait_ticks = 0  # Repartir d'un historique d'attente propre
                    self._fresh_ticks = 0
                    self._calm_streak = 0
                else:
                    self.consecutive_buy_errors += 1
                    if self.consecutive_buy_errors >= 5:
                        logger.critical("⛔ 5 achats consécutifs en échec — arrêt du bot "
                                        "(vérifiez les erreurs d'achat ci-dessus)")
                        self.running = False
        else:
            # En position - gérer le trade
            self.tick_count += 1

            # Vérifier Knock-out (tick par tick, comme le contrat réel)
            if await self.check_knockout():
                return

            # Garde-fou temporel: le contrat n'a pas d'échéance, si le flux de
            # ticks ralentit ou si le TP est raté, on force la sortie.
            elapsed = time.time() - self.entry_time
            if elapsed > config.MAX_POSITION_SECONDS:
                logger.warning(f"⏱️ Position ouverte depuis {elapsed:.0f}s — vente de sécurité")
                await self.sell_contract(f"Timeout sécurité ({elapsed:.0f}s)")
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

    async def _message_loop(self):
        """Boucle de réception WebSocket. Sort normalement quand self.running
        passe à False; lève une exception en cas de perte de connexion."""
        last_ping = time.time()
        while self.running:
            if not self.ws.connected:
                raise websocket.WebSocketException("Connexion WebSocket perdue")

            # Keepalive: ping toutes les 20 s
            if time.time() - last_ping > 20:
                self.ws.send(json.dumps({"ping": 1}))
                last_ping = time.time()

            try:
                message = self.ws.recv()
            except websocket.WebSocketTimeoutException:
                continue

            data = json.loads(message)

            # Gérer les différents types de messages
            if "tick" in data:
                await self.process_tick(data["tick"])
            elif data.get("msg_type") == "ping":
                pass  # Pong du keepalive
            elif "error" in data:
                logger.error(f"Erreur API: {data['error']['message']}")

            await asyncio.sleep(0.1)  # Petit délai pour éviter CPU spike

    async def run(self):
        """Boucle principale: connexion, réception, reconnexion automatique."""
        mode = "OBSERVATION (simulation)" if not self.token else "TRADING"
        logger.info(f"🚀 Démarrage du Bot Accumulator... [mode: {mode}]")

        consecutive_failures = 0
        try:
            while self.running:
                stable_since = time.time()
                try:
                    await self.connect()

                    # Position restée ouverte après une déconnexion: on la
                    # clôture immédiatement (les données locales sont périmées).
                    if self.contract_id and not self.observation_mode:
                        logger.warning("Position restée ouverte après reconnexion — "
                                       "vente immédiate")
                        await self.sell_contract("Reconnexion")

                    await self._message_loop()
                except Exception as e:
                    if not self.running:
                        break
                    self.is_connected = False
                    # Une session stable de plus de 60 s remet le compteur à zéro
                    if time.time() - stable_since > 60:
                        consecutive_failures = 0
                    consecutive_failures += 1
                    if consecutive_failures > 5:
                        logger.critical(f"⛔ {consecutive_failures - 1} échecs de connexion "
                                        "successifs — arrêt du bot")
                        self.running = False
                        break
                    logger.error(f"Erreur: {e} — reconnexion dans 5s "
                                 f"(tentative {consecutive_failures}/5)")
                    await asyncio.sleep(5)

        except KeyboardInterrupt:
            logger.info("\n⛔ Arrêt manuel demandé...")
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

    if not acquire_lock():
        print("ERREUR: Une autre instance du bot tourne déjà (fichier bot.lock).")
        print("Deux instances sur le même compte se bloquent mutuellement. Arrêt.")
        sys.exit(1)

    bot = AccumulatorBot()

    print(f"Mode DEBUG: {config.DEBUG_MODE}")
    print(f"Symbole: {bot.symbol}")
    print(f"Compte: {bot.account_type}")
    print(f"Mise: ${bot.stake}")
    print(f"Analyse volatilité: {config.VOLATILITY_PERIOD} ticks, "
          f"cooldown {config.COOLDOWN_TICKS} ticks après clôture")
    print(f"Objectif ticks: {config.TARGET_TICKS_MIN}-{config.TARGET_TICKS_MAX}")
    print("-" * 40)

    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        print("\nArrêt propre effectué.")
