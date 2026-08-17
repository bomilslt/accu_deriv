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

TREND_FILTER_ENABLED = True   # Exiger une micro-tendance avant d'entrer
TREND_WINDOW = 10             # Fenêtre de ticks analysés
TREND_DIRECTIONALITY = 0.7    # Fraction de ticks dans la même direction requise
TREND_MAX_WAIT_TICKS = 0      # 0 = attendre indéfiniment; sinon fallback après X ticks

REOBSERVE_TICKS = 25      # ticks frais à collecter après une clôture avant d'évaluer
CALM_CONFIRM_TICKS = 5    # ticks consécutifs "calmes" requis (un tick brutal remet à 0)
CALM_MAX_TICK_MOVE = 0.0  # variat. max par tick jugée "calme" (0 = auto: barrière la plus large)

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

### Filtre de tendance (condition d'entrée)

Pour ne pas se relancer dans un marché qui oscille dès la fin du cooldown,
le bot exige en plus une **micro-tendance directionnelle** avant d'acheter :
il mesure la fraction de ticks haussiers sur les `TREND_WINDOW` derniers ticks
(10 par défaut).

```
Fraction de ticks haussiers sur les 10 derniers ticks:
  >= 70%  -> tendance haussière ✅  (le marché "marche" dans une direction)
  <= 30%  -> tendance baissière ✅  (les deux conviennent à l'ACCU)
  sinon   -> marché qui oscille en va-et-vient, on reste hors du marché ⏳
```

Tant qu'aucune tendance n'est détectée, le bot **n'achète pas** et continue
d'analyser le marché. Avec `TREND_MAX_WAIT_TICKS = 0` (défaut), il attend
le signal indéfiniment ; avec une valeur > 0, il reprend le comportement
actuel (entrée dès qu'un taux est sûr) après ce nombre de ticks d'attente.

Cela évite le problème principal : de racheter "à l'instant T" au premier
tick sûr par volatilité, sans avoir vérifié que le marché est calme **et**
directionnel — c'est-à-dire dans le régime où l'ACCU encaisse du gain
tick après tick au lieu de casser sa barrière.

### Ré-observation et confirmation de calme

Après chaque clôture, le bot ne se relance pas au premier tick "sûr".
Il ré-étudie le marché avant de reconsidérer une entrée :

```
Clôture d'un trade
  → Cooldown (COOLDOWN_TICKS, simple pause)
  → Ré-observation (REOBSERVE_TICKS):
      attendre au moins ce nombre de ticks FRAIS (post-clôture)
      pour que la fenêtre de volatilité soit 100% à jour
  → Confirmation de calme (CALM_CONFIRM_TICKS):
      N ticks CONSÉCUTIFS "calmes" requis
        - variation du tick ≤ CALM_MAX_TICK_MOVE
          (0 = auto: la barrière la plus large = aucun à-coup brutal)
        - et un taux sûr existe (volatilité dans une fenêtre fraîche)
      ⚠️ un tick trop brutal remet le compteur à ZÉRO (le marché n'est
         pas encore stable: on reprend l'étude)
  → Filtre de tendance (voir ci-dessus)
  → Entrée
```

Résultat : après une perte, le bot reste hors du marché **~30 ticks ou plus**
(~30 s), et ne rentre que si le calme est *réel et soutenu* — pas sur un
seul instant de volatilité basse.

### Gestion du trade
- **Entrée**: Achat automatique quand le calme est confirmé (ré-observation + N ticks calmes) **et** que le marché montre une micro-tendance directionnelle **et** qu'un taux est sûr par volatilité
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
