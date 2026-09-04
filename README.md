<div align="center">
  <img src="https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/Flask-000000?style=for-the-badge&logo=flask&logoColor=white" />
  <img src="https://img.shields.io/badge/Razorpay-02042B?style=for-the-badge&logo=razorpay&logoColor=0753FF" />
  <img src="https://img.shields.io/badge/AI-Reconciliation-34D399?style=for-the-badge" />
</div>

<br />

<div align="center">
  <img src="https://github.com/user-attachments/assets/b8ed3ab1-d2ab-46e3-ae32-5a4cbcfc24e5" alt="AI Finance Controller Hero Image" width="800" />
</div>

# AI Finance Controller

A premium, AI-powered financial reconciliation dashboard designed to automate the mapping of internal financial ledgers with external payment gateways like Razorpay. It features a cutting-edge **glassmorphism UI** with a high-contrast dark theme, combining powerful Python backend analytics with an immersive frontend experience.

## ✨ Key Features

- **Automated Reconciliation**: Instantly reconcile synthetic financial records against transaction datasets.
- **AI QA Agent**: Embedded AI assistant capable of answering deep analytical questions about your financial data and identifying discrepancies.
- **Razorpay Integration**: Seamlessly connect and fetch real-time transaction data from Razorpay endpoints.
- **Dynamic Port Configuration**: Run the app on any custom port seamlessly via CLI arguments (e.g., `python app.py 5005`).
- **Premium UI/UX**: Built from the ground up with a custom design system featuring:
  - Deep Dark Theme (`#1A1A1A` backgrounds).
  - High-visibility Typography (`#F0F0F0`).
  - Dark Teal & Deep Ruby interactive gradients.
  - Glowing Forest Green CTA elements.

## 🚀 Quick Start

### 1. Prerequisites
- Python 3.8+
- Node.js (optional, for running localtunnel)

### 2. Installation
Clone the repository and install the required dependencies:

```bash
git clone https://github.com/Shikhar28-web/ai-finance-controller.git
cd ai-finance-controller
pip install -r requirements.txt
```

### 3. Running the Server
You can start the server on the default port (`5000`), or specify a custom port:

```bash
# Run on default port (5000)
python app.py

# Run on a custom port (e.g., 5005)
python app.py 5005
```

### 4. Exposing Globally (For Remote Mentors / Testing)
If you need to share the live dashboard securely across the internet, you can tunnel the local port:

```bash
# Using Localtunnel (Recommended)
npx localtunnel --port 5005
```
*Note: Localtunnel will provide a public URL. The "Tunnel Password" is your public IP address, which you can find at [api.ipify.org](https://api.ipify.org).*

## 🧠 Architecture

- **Backend**: Flask (Python) handles the API routing, data synthetic generation, and AI agent logic.
- **Frontend**: Pure Vanilla HTML/CSS/JS ensuring lightning-fast load times. The UI uses heavy custom CSS variables for easy theming without the overhead of external libraries.
- **Data Layer**: Processes `.csv` statements and dynamically updates `metrics_summary.json` for persistent visual charting.

## 🎨 Design System

The application strictly adheres to a high-contrast, professional fintech aesthetic:
- **Background**: `#1A1A1A` (Rich Black)
- **Primary Text**: `#F0F0F0` (Off-White)
- **Brand Gradients**: Dark Teal (`#004D61`) to Deep Ruby (`#822659`)
- **Actions/CTAs**: Forest Green (`#3E5641`)

## 📄 License

This project is open-source and available under the MIT License.
