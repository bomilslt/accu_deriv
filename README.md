# Bot de Trading Accumulator pour Deriv

Bot automatique de trading d'Accumulators sur la plateforme Deriv, avec stratégie de scalping intelligent.

## 🎯 Stratégie

Ce bot implémente une logique de trading conservative basée sur :

1. **Analyse de volatilité en temps réel** : Calcule l'écart-type des variations de prix sur les derniers ticks
2. **Sélection dynamique du taux** : Choisit automatiquement le taux de croissance le plus élevé dont la barrière est sûre par rapport à la volatilité actuelle
3. **Take Profit rapide (4-5 ticks)** : Sortie automatique après quelques ticks pour sécuriser les gains
4. **Protection anti-mouvements brusques** : Détection et sortie anticipée en cas de mouvement anormal du marché

## 📋 Prérequis

- Python 3.8+
- Compte Deriv (gratuit sur [deriv.com](https://deriv.com))
- Une application enregistrée sur [developers.deriv.com](https://developers.deriv.com) (donne un **App ID alphanumérique**)
- Un **Personal Access Token** au format `pat_...` (généré depuis votre compte Deriv, avec les droits lecture + trading)

## 🚀 Installation

```bash
# Installer les dépendances
pip install websocket-client python-dotenv

# Copier le fichier d'exemple et configurer vos identifiants
cp .env.example .env

# Éditer .env : ajouter votre App ID et votre token pat_...
nano .env
```

> ⚠️ **Important (nouvelle API Deriv)** : le token `pat_...` n'est affiché
> **qu'une seule fois** à sa création. Copiez-le **entièrement** immédiatement.
> Un token tronqué (ou l'ancien format `a1-...`) sera refusé.
> Sans token, le bot démarre en mode observation (WebSocket public, trades simulés).

## 🔐 Authentification (nouvelle API 2026)

Le bot utilise la nouvelle API Deriv :

1. **REST** `GET https://api.derivws.com/trading/v1/options/accounts` avec les headers
   `Authorization: Bearer pat_...` et `Deriv-App-ID: <votre app id>` (liste des comptes)
2. **REST** `POST .../accounts/{accountId}/otp` → renvoie une URL WebSocket à usage unique
3. **WebSocket** : connexion à cette URL (l'OTP fait office d'authentification), puis
   messages au format habituel (`ticks`, `buy` avec `contract_type: ACCU`, `sell`, `ping`)

Les anciens identifiants (App ID numérique + token `a1-...` de l'API v3
`ws.binaryws.com`) ne sont plus utilisables.

## ⚙️ Configuration

Les paramètres de trading (compte, marché, mise) se définissent dans le `.env` :

```ini
DERIV_ACCOUNT_TYPE=demo  # "demo" ou "real"
DERIV_SYMBOL=R_100       # marché (R_10, R_25, R_50, R_75, R_100...)
DERIV_STAKE=10           # mise par trade en USD
```

Les paramètres de stratégie se règlent dans `config.py` :

```python
# Paramètres de stratégie
VOLATILITY_PERIOD = 20  # Fenêtre d'analyse de la volatilité (>= 20 recommandé)
VOLATILITY_MULTIPLIER = 2.5  # Coefficient de sécurité

COOLDOWN_TICKS = 10  # Ticks d'attente après une clôture avant de ré-entrer
MAX_POSITION_SECONDS = 30  # Vente forcée si la position dure trop (ticks bloqués)

TARGET_TICKS_MIN = 4  # Sortie min après 4 ticks
TARGET_TICKS_MAX = 5  # Sortie max après 5 ticks

ABNORMAL_MOVE_THRESHOLD = 3.0  # Seuil de détection mouvement anormal

# Logs
DEBUG_MODE = True  # True pour tests locaux, False pour VPS
```

## ▶️ Lancement

### Mode Test (avec logs détaillés)
```bash
python accu_bot.py
```

### Mode Production (VPS, logs réduits)
1. Mettre `DEBUG_MODE = False` dans `config.py`
2. Lancer en background :
```bash
nohup python accu_bot.py > /dev/null 2>&1 &
```

## 📊 Fonctionnement

### Algorithme de sélection de barrière
```
Volatilité = Écart-type(variations %) sur N ticks
Seuil Sécurité = Volatilité × 2.5

Pour chaque taux (du plus élevé au plus bas):
    Si Barrière(Taux) > Seuil Sécurité:
        ✅ Sélectionner ce taux
        Break
Sinon:
    ⚠️ Ne pas trader (trop risqué)
```

### Gestion du trade
- **Entrée**: Achat automatique quand un taux sûr est détecté
- **Sortie normale**: Après 4-5 ticks (Take Profit)
- **Sortie urgente**: Si mouvement anormal détecté (> 3× la volatilité)
- **Knock-out**: Perte automatique si le prix touche la barrière

## 📝 Logs

Les logs sont écrits dans `trading_bot.log` (toujours activé pour traçabilité).

- **Mode DEBUG**: Affiche tous les détails en console + fichier
- **Mode INFO**: Seulement les événements importants en console, détails dans le fichier

## ⚠️ Avertissements

- Ce bot est fourni à titre éducatif uniquement
- Le trading comporte des risques de perte en capital
- Testez toujours en mode démo avant d'utiliser de l'argent réel
- Les performances passées ne garantissent pas les résultats futurs

## 🔧 Personnalisation

Vous pouvez modifier :
- Les paires tradables (`SYMBOL` dans config)
- Les taux et barrières disponibles (`BARRIER_OPTIONS`)
- La sensibilité de détection de volatilité
- Les seuils de Take Profit

## 📞 Support API Deriv

Documentation officielle (nouvelle API) : https://developers.deriv.com/
