/**
 * API Service for SymbioLink AI Mobile App
 * Handles all API communication with the backend
 *
 * KNOWN GAP: the Flask backend (app.py) authenticates every @login_required route
 * with a Flask-Login *session cookie*, not a bearer token -- this.token below is
 * sent as `Authorization: Bearer ...`, which app.py never reads. AuthContext.login()
 * is also still a mock (see src/context/AuthContext.js) and never actually calls
 * api.login(). So today, every call in this file that needs @login_required
 * (orders, payments, disputes) will 401/redirect against a real backend even
 * though login "succeeds" in the app. Fixing this needs either (a) switching the
 * fetch calls here to `credentials: 'include'` plus a cookie jar library, since
 * plain fetch on React Native doesn't persist cookies across requests the way a
 * browser does, or (b) adding a real token-auth layer (e.g. Flask-JWT) alongside
 * the existing session-cookie auth the web app uses. Out of scope for the
 * payment/dispute methods added here -- they're written to call the *correct*
 * endpoints with the *correct* payloads, but need one of the above before they'll
 * authenticate against the real backend.
 */

const API_BASE_URL = 'http://localhost:5000'; // Update for production

class ApiService {
  constructor() {
    this.baseURL = API_BASE_URL;
    this.token = null;
  }

  setToken(token) {
    this.token = token;
  }

  getHeaders() {
    const headers = {
      'Content-Type': 'application/json',
    };
    if (this.token) {
      headers['Authorization'] = `Bearer ${this.token}`;
    }
    return headers;
  }

  async request(endpoint, options = {}) {
    const url = `${this.baseURL}${endpoint}`;
    const config = {
      ...options,
      headers: {
        ...this.getHeaders(),
        ...options.headers,
      },
    };

    try {
      const response = await fetch(url, config);
      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.message || 'Request failed');
      }

      return data;
    } catch (error) {
      console.error('API Error:', error);
      throw error;
    }
  }

  // Auth endpoints
  async login(username, password) {
    const formData = new FormData();
    formData.append('username', username);
    formData.append('password', password);
    
    return this.request('/login', {
      method: 'POST',
      headers: {},
      body: formData,
    });
  }

  async register(userData) {
    return this.request('/register', {
      method: 'POST',
      body: JSON.stringify(userData),
    });
  }

  // Listings endpoints
  async getListings() {
    return this.request('/listings');
  }

  async searchListings(query, category) {
    const params = new URLSearchParams();
    if (query) params.append('q', query);
    if (category) params.append('category', category);
    return this.request(`/search?${params.toString()}`);
  }

  async getListingDetail(unitId, material) {
    return this.request(`/listings/${unitId}/${material}`);
  }

  // Matches endpoints
  async getMatches() {
    return this.request('/matches');
  }

  // Orders endpoints
  async getOrders() {
    // /orders (no id) renders the full HTML page on the web app -- /api/orders
    // is the JSON list route added alongside /api/order/<id>.
    return this.request('/api/orders');
  }

  async createOrder(orderData) {
    return this.request('/order/new', {
      method: 'POST',
      body: JSON.stringify(orderData),
    });
  }

  async getOrderDetail(orderId) {
    // /orders/<id> doesn't exist as a route -- /orders (no id) renders the whole
    // HTML list. /api/order/<id> is the actual JSON single-order endpoint.
    return this.request(`/api/order/${orderId}`);
  }

  async advanceOrder(orderId) {
    return this.request(`/order/${orderId}/advance`, {
      method: 'POST',
    });
  }

  async completeOrder(orderId, ratings) {
    return this.request(`/order/${orderId}/complete`, {
      method: 'POST',
      body: JSON.stringify(ratings),
    });
  }

  // Payment endpoints -- mirror the web app's flow in _pay_script.html:
  // createPaymentOrder() opens a Razorpay order against a SymbioLink order,
  // verifyPayment() confirms it afterwards. Both accept JSON bodies on the Flask
  // side (see /payment/create-order and /payment/verify in app.py).
  async createPaymentOrder(orderId) {
    return this.request('/payment/create-order', {
      method: 'POST',
      body: JSON.stringify({ order_id: orderId }),
    });
  }

  async verifyPayment({ razorpayOrderId, razorpayPaymentId, razorpaySignature }) {
    return this.request('/payment/verify', {
      method: 'POST',
      body: JSON.stringify({
        razorpay_order_id: razorpayOrderId,
        razorpay_payment_id: razorpayPaymentId,
        razorpay_signature: razorpaySignature,
      }),
    });
  }

  // Dispute endpoint -- buyer flags a problem with a paid order for admin review.
  async raiseDispute(orderId, reason) {
    return this.request(`/order/${orderId}/dispute`, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    });
  }

  // Units endpoints
  async getUnits() {
    return this.request('/units');
  }

  async getUnitDetail(unitId) {
    return this.request(`/units/${unitId}`);
  }

  // Predictions endpoint
  async getPredictions() {
    return this.request('/predictions');
  }

  // Pooling endpoint
  async getPoolingInfo() {
    return this.request('/pooling');
  }

  // Trust endpoint
  async getTrustInfo() {
    return this.request('/trust');
  }
}

export default new ApiService();