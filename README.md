# Noèse, le Jardinier

Un outil du Lab **état de l'art** pour garder à jour la base de connaissances qu'utilisent les chatbots, callbots et assistants de réponse d'une collectivité.

Noèse ne parle pas aux habitants. Il aide les agents à voir ce que la base ne sait pas, à demander la bonne information au bon service, et à garder les fiches fraîches. La base validée s'exporte vers n'importe quel outil (chatbot, callbot, assistant mail).

> Nom de travail. Logiciel libre sous licence EUPL 1.2.

## Ce qui marche aujourd'hui

**Repérer les trous (tranche a)**
- **Import des questions d'habitants** : CSV (export de chatbot ou de callbot, colonnes reconnues automatiquement ou choisies à la main), mails `.eml` et `.mbox`, copier-coller.
- **Import de la base existante** : site de la ville (lecture limitée au domaine et au chemin de départ, 200 pages au maximum), PDF, Markdown, texte.
- **Analyse** : les questions sont reformulées en besoins, regroupées, nommées et rattachées à un service, puis chaque groupe est vérifié dans la base et classé **couvert**, **partiel** ou **trou**.
- **Suivi de l'analyse** en direct sur l'écran d'import.

**Tableau des trous (tranche b)**
- Liste des besoins du plus demandé au moins demandé, avec le nombre de questions, celles des 30 derniers jours et la part passée à un agent.
- Filtres par service, par statut (à traiter, trous, partiels, couverts, demandés, écartés) et recherche plein texte. Les filtres fonctionnent aussi sans JavaScript, et l'URL garde les filtres.
- Détail d'un besoin : toutes les vraies questions, ce que la base répond, ce qui manque et les sources citées.
- Actions du référent base : changer le service, corriger le libellé, rassembler deux besoins, vérifier à nouveau dans la base, écarter ou rouvrir. Chaque action est journalisée.
- Une nouvelle analyse ne défait pas le travail des agents : les questions rassemblées à la main, et celles des besoins déjà demandés ou écartés, ne sont plus déplacées.

**Demande, réponse et validation (tranche c)**
- Depuis un besoin, le référent base envoie une **demande précise** au service : la question est proposée par l'IA, relue et modifiée par l'agent.
- Le référent métier reçoit un mail avec un **lien magique** (sans compte ; seul un hachage du jeton est stocké).
- Sa page, pensée d'abord pour le mobile, montre la question et de vraies questions d'habitants, et accepte une réponse écrite ou **dictée** (enregistrement converti en WAV dans le navigateur, transcription par le modèle configuré).
- L'IA en fait une **fiche** (titre, réponse, conditions, pièces, lien, date de vérification). Le référent métier la relit à côté de sa réponse d'origine, la corrige et la **valide avec son nom**. Un lien que l'agent n'a pas écrit est retiré d'office.
- « Je ne suis pas la bonne personne » transmet la question à un collègue, avec un nouveau lien.
- Une fiche validée entre dans la base, est citée comme source et ferme le trou. Péremption : 6 mois, ou 1 mois pour un tarif, une date ou une saison.
- Sans serveur SMTP (`NOESE_SMTP_HOST=journal`), ou si l'envoi échoue, le lien s'affiche sur la page du besoin pour être transmis autrement.

**Fraîcheur et relances (tranche d)**
- Chaque fiche a un **service propriétaire** et une **date de vérification** : 6 mois par défaut, 1 mois pour un tarif, une date ou une saison. Ces durées se règlent dans les Paramètres.
- Avant l'échéance (14 jours avant, 7 pour une fiche saisonnière), le référent du service reçoit un mail : **« Toujours juste »** prolonge la fiche d'un clic, ou bien il la corrige (nouvelle version validée), ou il la retire (elle sort de la base et le sujet redevient un trou).
- Sans réponse au bout de **5 jours ouvrés**, une relance douce part avec le même lien. Sans réponse 5 jours ouvrés plus tard, le **référent de la base** reçoit un signalement, et l'élément apparaît dans « À suivre » sur l'accueil. Le calcul tient compte des week-ends et des jours fériés.
- Les mails ne partent que les jours ouvrés, de 8 h à 18 h. L'échéancier tourne dans le worker toutes les 15 minutes ; on peut aussi le lancer depuis les Paramètres. Chaque étape est horodatée : rien ne part deux fois.
- **Base de fiches** : recherche, filtre par service propriétaire et par fraîcheur, détail avec l'historique des versions et des vérifications, changement de propriétaire.
- **Paramètres** : services et référents, durées et délais, référent de la base, derniers mails envoyés.
- Les liens magiques ne sont jamais stockés : ils se recalculent à partir de `NOESE_SECRET` et d'un sel. Changer ce secret invalide tous les liens en cours.

**Tableau de bord et export (tranche e)**
- L'accueil donne **quatre chiffres** : taux de couverture des questions, trous ouverts, fiches périmées, et délai médian de réponse des services en jours ouvrés. Pour une question transmise à un collègue, le délai court depuis le premier envoi.
- **« Où un coup de main serait utile »** : pour chaque service, les besoins à traiter, les demandes en cours, les fiches déjà apportées et le délai de réponse. Pas de couleur ni de rang : le tableau sert à proposer de l'aide.
- **Export** de la base validée en **Markdown** (une section par fiche, lisible et découpable pour un RAG), **JSON** et **CSV** (point-virgule, UTF-8, s'ouvre dans un tableur), depuis les Paramètres ou à l'adresse `/export/base.md|json|csv`.
  - Seules les fiches validées sortent. Les périmées sont signalées, ou exclues avec `?perimees=0`.
  - Chaque fiche porte sa source, la personne qui l'a validée, la date de validation, la date de vérification, et des exemples de questions d'habitants choisis au moment de la validation.
- **Réimport** : les trois fichiers se réimportent depuis l'écran Importer. Les fiches gardent leur validation d'origine, et celles qui n'en ont pas sont refusées. Les tests vérifient qu'un aller-retour ne perd aucune information, quel que soit le format.

## Critères d'acceptation

| Critère | État |
|---|---|
| Au moins 80 % des trous volontaires retrouvés, chaque « couvert » cite une source réelle | 10 trous sur 10 lors de trois évaluations successives, aucun « couvert » sans source (`scripts/evaluer.py`) |
| Du mail à la fiche validée en moins de 3 minutes sur mobile | 57 s et 54 s lors de deux parcours chronométrés (saisie au clavier, dont 6 à 8 s d'attente du modèle) |
| Aucune fiche dans la base sans validation humaine | Contrainte en base, plus des tests sur la proposition, la validation sans nom, le réimport et l'export |
| Export JSON et Markdown lisibles et réimportables | Tests d'aller-retour sur Markdown, JSON et CSV |
| Contrastes 4,5:1 dans les deux thèmes, navigation au clavier | `scripts/contrastes.py` (tous au-dessus de 4,5:1) ; parcours au clavier vérifiés sur la liste des trous et la page de réponse |
| `docker compose up` lance tout, README en français | Testé avec Docker Desktop 29 et Compose 5 : construction, base, app, worker et Mailpit démarrent. Parcours complet vérifié dans les conteneurs : connexion par un vrai mail, analyse de la démo par le worker, demande reçue dans Mailpit, dictée, validation, export |

## Limites connues

- Les évolutions de schéma passent par de petites migrations au démarrage (`app/migrations.py`), à reprendre dans Alembic avant les pilotes.
- Avec un petit modèle (`ministral-8b`), le regroupement de questions proches est parfois trop fin (l'outil « Rassembler » y remédie), et la mise en forme ajoute parfois une phrase : la relecture affiche la réponse d'origine de l'agent pour la repérer.
- La dictée n'a été testée qu'avec un fichier audio, pas avec un vrai micro sur téléphone.

**Connexion du référent base**
- Les écrans d'administration (accueil, import, trous, fiches, paramètres, export) demandent une connexion. Le référent tape son adresse et reçoit un **lien valable 15 minutes, utilisable une seule fois**. Il n'y a pas de mot de passe.
- La session dure 30 jours sur le poste. Retirer un référent dans les Paramètres coupe sa session immédiatement.
- La page répond toujours la même chose, que l'adresse soit connue ou non. Elle envoie au plus 5 liens par heure et par référent, et ne redirige jamais vers un autre site après la connexion.
- Les services n'ont toujours pas de compte : leurs pages (`/r/…`, `/v/…`) restent accessibles par leur propre lien.
- Le premier référent est créé au démarrage depuis `NOESE_REFERENT_BASE_EMAIL`. Les suivants s'ajoutent dans les Paramètres.
- En développement, sans serveur de mail (`NOESE_SMTP_HOST=journal`), le lien s'affiche à l'écran, avec la mention « Mode développement ».

## Démarrer avec Docker

```bash
cp .env.example .env   # puis renseignez NOESE_LLM_API_KEY, NOESE_SECRET et NOESE_REFERENT_BASE_EMAIL
docker compose up
```

- Application : http://localhost:8000
- Boîte mail de test : http://localhost:8025 (le lien de connexion du référent y arrive)

Prévoyez environ 4 Go libres sur le disque pour les images. Les conteneurs sont à l'heure de Paris (`TZ`), ce qui compte pour les relances, envoyées de 8 h à 18 h.

Ne placez pas le projet dans un dossier synchronisé (iCloud Drive, OneDrive, Dropbox) : la base locale de développement et le dépôt git supportent mal la synchronisation.

Sur l'écran **Importer**, le bouton « Charger les données de démo » installe Ville-Exemple, une commune fictive : 32 pages de site et 309 questions d'habitants.

## Démarrer sans Docker (développement)

Il faut [uv](https://docs.astral.sh/uv/). Sans `NOESE_DATABASE_URL`, un PostgreSQL embarqué avec pgvector (paquet `pgserver`) démarre tout seul dans `.pgdata/`.

```bash
uv sync
uv run uvicorn app.main:app --reload
```

Le worker tourne alors dans le même processus. Avec Docker, il tourne dans son propre conteneur (`python -m app.taches`).

## Principes

1. **L'IA prépare, l'agent valide.** Une contrainte de la base de données interdit d'enregistrer une version de fiche validée sans nom de validateur ni date.
2. **Toujours une source.** Un groupe n'est classé « couvert » ou « partiel » que s'il cite au moins un extrait réellement trouvé dans la base. Sinon, c'est un trou.
3. **Souverain.** Un seul format d'API (compatible OpenAI) pour changer de fournisseur : Mistral par défaut, l'API Albert de l'État ou un modèle auto-hébergé. Les polices et les scripts sont servis par l'application, sans appel à un CDN.
4. **Léger.** FastAPI, gabarits rendus côté serveur, HTMX (50 Ko), une seule base PostgreSQL (qui sert aussi de file de tâches), aucun framework front.
5. **Accessible.** RGAA comme plancher : contrastes vérifiés dans les deux thèmes, focus visible, navigation au clavier, libellés explicites.

## Fournisseur de modèles

| Variable | Rôle |
|---|---|
| `NOESE_LLM_BASE_URL` | URL de l'API compatible OpenAI |
| `NOESE_LLM_API_KEY` | Clé d'API |
| `NOESE_LLM_CHAT_MODEL` | Modèle de dialogue (reformulation, regroupement, jugement) |
| `NOESE_LLM_EMBED_MODEL` | Modèle d'embeddings, ou `local` pour des embeddings lexicaux calculés sur place |
| `NOESE_LLM_STT_MODEL` | Modèle de transcription vocale |

Changer de modèle d'embeddings déclenche une réindexation complète à la prochaine analyse.

La recherche dans la base est hybride : la similarité vectorielle (pgvector) est combinée avec la recherche plein texte en français de PostgreSQL. L'outil reste ainsi fiable avec des embeddings modestes.

## Vérifier la qualité

```bash
uv run pytest                                    # tests hors ligne, fournisseur factice
uv run python -m scripts.evaluer --reinitialiser # évaluation sur Ville-Exemple, avec le vrai modèle
```

L'évaluation compare les verdicts à `demo/verite_terrain.yaml` : au moins 80 % des trous volontaires doivent être retrouvés, et chaque verdict « couvert » doit citer une source réelle.

## Arborescence

```
app/            application (modèles, analyse, import, worker, pages)
app/templates/  gabarits Jinja
app/static/     CSS, polices Jost et Inter, HTMX
demo/           Ville-Exemple : site, questions, mails, vérité terrain
scripts/        génération des données de démo, évaluation
tests/          tests automatisés
```

## Licence

Noèse est un logiciel libre publié par état de l'art sous la [licence publique de l'Union européenne (EUPL) v. 1.2](LICENSE). Le texte officiel en français est dans le fichier `LICENSE` ; les versions de l'EUPL dans toutes les langues de l'Union ont la même valeur.

Les polices Jost et Inter sont distribuées sous licence SIL Open Font License 1.1, et HTMX sous licence BSD zéro clause (0BSD).
