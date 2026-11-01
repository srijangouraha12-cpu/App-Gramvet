# 🐄 GramVet — AI-Powered Cattle Disease Surveillance & Prediction

GramVet is an AI-powered livestock health surveillance platform designed to help farmers and veterinary professionals **identify potential cattle diseases early, assess disease risk, and make informed healthcare decisions**.

The platform combines **machine learning-based disease prediction**, animal health information, vaccination history, symptoms, herd-level information, and environmental/spatiotemporal factors to provide actionable insights.

---

## 🎯 Problem Statement

Livestock plays a major role in the livelihood of rural communities. However, farmers often face challenges such as:

* 🐄 Difficulty identifying diseases at an early stage
* 🩺 Limited access to veterinary professionals
* 💉 Incomplete or irregular vaccination records
* 🌦️ Environmental factors increasing disease risk
* 📍 Lack of localized disease surveillance
* 📊 Limited access to data-driven livestock healthcare

Late identification of diseases can result in reduced milk production, economic losses, and increased disease transmission within herds.

**GramVet aims to bridge this gap through an accessible AI-assisted livestock healthcare platform.**

---

# 💡 Our Solution

GramVet provides a centralized platform where farmers can maintain animal health information and receive AI-assisted disease risk predictions.

The system considers information such as:

* Animal details
* Symptoms
* Herd size
* Vaccination history
* Water-body proximity
* Environmental/spatiotemporal information
* Other relevant health parameters

The collected information is processed by our machine-learning pipeline to generate disease predictions and surveillance insights.

---

# ✨ Key Features

### 🐮 Animal Health Management

* Add and manage livestock records
* Maintain animal health information
* Update animal details over time
* Track vaccination history

### 🩺 Symptom-Based Disease Prediction

* Enter observed symptoms
* Submit animal health parameters
* ML-based disease prediction
* Disease-risk assessment

### 💉 Vaccination Tracking

* Record vaccination dates
* Automatically calculate vaccination recency
* Monitor vaccination status
* Identify potentially overdue vaccinations

### 🌾 Herd & Environmental Information

* Herd-size information
* Distance from the nearest water body
* Environmental factors relevant to disease surveillance

### 📍 Spatiotemporal Surveillance

* Incorporates geographical and time-related information
* Supports disease surveillance at the herd/community level
* Helps identify patterns that may contribute to disease spread

### 🌐 Farmer-Friendly Interface

* Simple and accessible interface
* Designed with rural users in mind
* Marathi language support
* Responsive web interface

### 🔐 User Management

* User registration and login
* Persistent storage of livestock information
* Individual farmer records

---

# 🏗️ System Architecture

```text
                    ┌──────────────────────┐
                    │      Farmer/User     │
                    └──────────┬───────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │   GramVet Web UI     │
                    │ HTML / CSS / JS       │
                    └──────────┬───────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │      Flask App       │
                    │      app.py          │
                    └───────┬───────┬──────┘
                            │       │
                ┌───────────┘       └────────────┐
                ▼                                ▼
      ┌──────────────────┐             ┌──────────────────┐
      │   ML Prediction  │             │    Database       │
      │   ml_package/    │             │  User/Animal Data │
      └─────────┬────────┘             └──────────────────┘
                │
                ▼
      ┌──────────────────┐
      │ Disease Prediction│
      │ & Risk Assessment │
      └──────────────────┘
```

---

# 📁 Project Structure

```text
GramVet-Disease_Decoders/
│
├── app.py
├── app_spatiotemporal.py
├── requirements.txt
├── README.md
├── run_windows.bat
│
├── templates/
│   └── index.html
│
├── ml_package/
│   ├── README.md
│   ├── predict_disease.py
│   └── bovine_surveillance_dataset_updated.csv
│
├── .gitignore
│
└── .venv/                  # Local virtual environment
```

> `.venv/` should remain local and should **not** be committed to GitHub.

---

# 🤖 Machine Learning

The ML component is responsible for analyzing livestock health and surveillance information and generating disease predictions.

### Input Parameters

Depending on the prediction model, the system can use parameters including:

| Category    | Parameters                                 |
| ----------- | ------------------------------------------ |
| Animal      | Animal identification/details              |
| Symptoms    | Observed symptoms                          |
| Herd        | Herd size                                  |
| Vaccination | Vaccination dates/history                  |
| Environment | Distance to nearest water body             |
| Time        | Vaccination recency / temporal information |
| Location    | Relevant geographical information          |

### Prediction Pipeline

```text
User Input
    │
    ▼
Data Validation
    │
    ▼
Feature Processing
    │
    ▼
ML Model
    │
    ▼
Disease Prediction
    │
    ▼
Risk / Health Report
```

The ML implementation and associated dataset are located inside:

```text
ml_package/
```

---

# 🗄️ Data & Database

GramVet stores relevant user and livestock information so that health records can be maintained over time.

Typical information includes:

* User information
* Animal information
* Symptoms
* Vaccination records
* Herd information
* Environmental information
* Prediction results

Sensitive credentials and secrets should be stored using environment variables rather than directly inside source code.

---

# 🛠️ Technology Stack

### Frontend

* HTML5
* CSS3
* JavaScript
* Responsive UI

### Backend

* Python
* Flask

### Machine Learning

* Python
* Scikit-learn / ML libraries used by the prediction pipeline
* Pandas
* NumPy
* joblib

### Database

* MongoDB / MongoDB Atlas
* SQLite3

### Development Tools

* Git
* GitHub
* Python Virtual Environment

---



# 🧪 Testing

Before deployment, test the following workflows:

* [ ] User registration
* [ ] User login
* [ ] Animal creation
* [ ] Animal information update
* [ ] Symptom input
* [ ] Vaccination date input
* [ ] Vaccination-month calculation
* [ ] Herd-size input
* [ ] Water-body distance input
* [ ] Disease prediction
* [ ] Report generation
* [ ] Database persistence
* [ ] Marathi and hindi  language interface for farmer
* [ ] Responsive design

---

# 🌱 Future Scope

GramVet can be expanded with:

* 📱 Android/mobile application
* 🎙️ Voice-based interaction for farmers
* 🗣️ Regional-language voice assistance
* 📸 Disease detection using animal images
* 🗺️ Village-level disease heatmaps
* 🔔 Vaccination reminders
* 🚨 Early disease outbreak alerts
* ☁️ Cloud-based livestock health records
* 👨‍⚕️ Veterinary expert consultation
* 📊 Advanced disease surveillance dashboards
* 🌦️ Integration with weather and environmental datasets

---


## Run on Windows
1. Open this folder in VS Code / terminal.
2. Run `run_windows.bat`.
3. The script creates `.venv`, installs `requirements.txt`, and starts `app.py`.
4. Open `http://127.0.0.1:5000`.

## Demo accounts
- Government: `+919000000001` / `Gov@26128`
- Vet One: `+919000000002` / `Vet@26128`
- Vet Two: `+919000000003` / `Vet@26128`
- Vet Three: `+919000000004` / `Vet@26128`
- Demo farmers: `+919000000005` through `+919000000014` / `Farmer@26128`

## ML package
`ml_package/` contains the trained Random Forest disease classifier, Decision Tree triage model, Logistic Regression escalation model, preprocessing artifacts and the dataset used to train them.

The prediction models themselves were not replaced with a new architecture; the Flask app now adapts the farmer/animal information to the bundled model's expected 35-feature input format.
