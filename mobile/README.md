# SymbioLink AI Mobile App

React Native mobile application for SymbioLink AI - Industrial Symbiosis Platform.

## Features

- Browse waste/need listings
- Search and filter listings
- View match opportunities
- Place and track orders
- Receive WhatsApp notifications
- Manage own listings
- Real-time updates

## Prerequisites

- Node.js (v14 or higher)
- React Native CLI
- Android Studio (for Android development)
- Xcode (for iOS development, macOS only)

## Installation

```bash
# Install dependencies
npm install

# For iOS
cd ios && pod install && cd ..

# Run on Android
npm run android

# Run on iOS
npm run ios
```

## Project Structure

```
mobile/
├── src/
│   ├── components/     # Reusable components
│   ├── screens/        # Screen components
│   ├── navigation/     # Navigation configuration
│   ├── services/       # API services
│   ├── context/        # React Context providers
│   └── utils/          # Utility functions
├── android/            # Android native code
├── ios/                # iOS native code
└── package.json
```

## API Configuration

Update the API base URL in `src/services/api.js`:

```javascript
const API_BASE_URL = 'http://localhost:5000/api'; // Development
// const API_BASE_URL = 'https://your-production-api.com/api'; // Production
```

## Authentication

The mobile app uses JWT tokens for authentication. Users can log in with their credentials from the web platform.

## Development

The app uses Expo for easier development and testing:

```bash
# Install Expo CLI
npm install -g expo-cli

# Start Expo development server
npm start
```

## Build for Production

### Android
```bash
cd android
./gradlew assembleRelease
```

### iOS
```bash
# Follow standard React Native iOS build process
```