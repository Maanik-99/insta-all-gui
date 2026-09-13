import json
import random
import uuid

# ==============================================================================
# REALISTIC BROWSER FINGERPRINT DATABASE
# ==============================================================================

WEBGL_PROFILES = [
    {
        "vendor": "Google Inc. (NVIDIA)",
        "renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "unmasked_vendor": "NVIDIA Corporation",
        "unmasked_renderer": "NVIDIA GeForce RTX 4070/PCIe/SSE2"
    },
    {
        "vendor": "Google Inc. (NVIDIA)",
        "renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "unmasked_vendor": "NVIDIA Corporation",
        "unmasked_renderer": "NVIDIA GeForce RTX 3060/PCIe/SSE2"
    },
    {
        "vendor": "Google Inc. (NVIDIA)",
        "renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3070 Ti Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "unmasked_vendor": "NVIDIA Corporation",
        "unmasked_renderer": "NVIDIA GeForce RTX 3070 Ti/PCIe/SSE2"
    },
    {
        "vendor": "Google Inc. (NVIDIA)",
        "renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 4080 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "unmasked_vendor": "NVIDIA Corporation",
        "unmasked_renderer": "NVIDIA GeForce RTX 4080/PCIe/SSE2"
    },
    {
        "vendor": "Google Inc. (NVIDIA)",
        "renderer": "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 SUPER Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "unmasked_vendor": "NVIDIA Corporation",
        "unmasked_renderer": "NVIDIA GeForce GTX 1660 SUPER/PCIe/SSE2"
    },
    {
        "vendor": "Google Inc. (AMD)",
        "renderer": "ANGLE (AMD, AMD Radeon RX 6700 XT Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "unmasked_vendor": "Advanced Micro Devices, Inc.",
        "unmasked_renderer": "AMD Radeon RX 6700 XT"
    },
    {
        "vendor": "Google Inc. (AMD)",
        "renderer": "ANGLE (AMD, AMD Radeon RX 7800 XT Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "unmasked_vendor": "Advanced Micro Devices, Inc.",
        "unmasked_renderer": "AMD Radeon RX 7800 XT"
    },
    {
        "vendor": "Google Inc. (AMD)",
        "renderer": "ANGLE (AMD, AMD Radeon(TM) Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "unmasked_vendor": "Advanced Micro Devices, Inc.",
        "unmasked_renderer": "AMD Radeon(TM) Graphics"
    },
    {
        "vendor": "Google Inc. (Intel)",
        "renderer": "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "unmasked_vendor": "Intel",
        "unmasked_renderer": "Intel(R) Iris(R) Xe Graphics"
    },
    {
        "vendor": "Google Inc. (Intel)",
        "renderer": "ANGLE (Intel, Intel(R) UHD Graphics 770 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "unmasked_vendor": "Intel",
        "unmasked_renderer": "Intel(R) UHD Graphics 770"
    }
]

CHROME_VERSIONS = [
    {"major": "133", "full": "133.0.6943.127", "build": "6943.127"},
    {"major": "133", "full": "133.0.6943.98",  "build": "6943.98"},
    {"major": "132", "full": "132.0.6834.160", "build": "6834.160"},
    {"major": "132", "full": "132.0.6834.111", "build": "6834.111"},
    {"major": "131", "full": "131.0.6778.205", "build": "6778.205"},
    {"major": "131", "full": "131.0.6778.86",  "build": "6778.86"},
    {"major": "130", "full": "130.0.6723.117", "build": "6723.117"},
    {"major": "130", "full": "130.0.6723.70",  "build": "6723.70"},
    {"major": "129", "full": "129.0.6668.101", "build": "6668.101"},
    {"major": "128", "full": "128.0.6613.138", "build": "6613.138"}
]

SCREEN_PROFILES = [
    {"width": 1920, "height": 1080, "availWidth": 1920, "availHeight": 1040, "dpr": 1.0, "colorDepth": 24},
    {"width": 1920, "height": 1080, "availWidth": 1920, "availHeight": 1040, "dpr": 1.25, "colorDepth": 24},
    {"width": 2560, "height": 1440, "availWidth": 2560, "availHeight": 1400, "dpr": 1.0, "colorDepth": 24},
    {"width": 2560, "height": 1440, "availWidth": 2560, "availHeight": 1400, "dpr": 1.25, "colorDepth": 24},
    {"width": 1680, "height": 1050, "availWidth": 1680, "availHeight": 1010, "dpr": 1.0, "colorDepth": 24},
    {"width": 1536, "height": 864,  "availWidth": 1536, "availHeight": 824,  "dpr": 1.25, "colorDepth": 24},
    {"width": 1440, "height": 900,  "availWidth": 1440, "availHeight": 860,  "dpr": 1.0, "colorDepth": 24},
    {"width": 1366, "height": 768,  "availWidth": 1366, "availHeight": 728,  "dpr": 1.0, "colorDepth": 24}
]

LANGUAGE_PROFILES = [
    {"primary": "en-US", "languages": ["en-US", "en"], "accept": "en-US,en;q=0.9"},
    {"primary": "en-GB", "languages": ["en-GB", "en-US", "en"], "accept": "en-GB,en-US;q=0.9,en;q=0.8"},
    {"primary": "en-CA", "languages": ["en-CA", "en-US", "en"], "accept": "en-CA,en-US;q=0.9,en;q=0.8"},
    {"primary": "en-AU", "languages": ["en-AU", "en-US", "en"], "accept": "en-AU,en-US;q=0.9,en;q=0.8"}
]

HARDWARE_CONCURRENCIES = [4, 6, 8, 12, 16, 24, 32]
DEVICE_MEMORIES = [4, 8, 16, 32]


class BrowserFingerprint:
    """Represents a complete, realistic, cycle-unique browser fingerprint"""
    def __init__(self):
        self.chrome = random.choice(CHROME_VERSIONS)
        self.webgl = random.choice(WEBGL_PROFILES)
        self.screen = random.choice(SCREEN_PROFILES)
        self.lang = random.choice(LANGUAGE_PROFILES)
        self.hardware_concurrency = random.choice(HARDWARE_CONCURRENCIES)
        self.device_memory = random.choice(DEVICE_MEMORIES)
        self.max_touch_points = 0
        
        # Windows OS version simulation (Windows 10 / Windows 11)
        self.is_win11 = random.choice([True, False])
        self.os_platform_version = "15.0.0" if self.is_win11 else "10.0.0"
        self.os_version_str = "Windows NT 10.0; Win64; x64"

        # Construct User-Agent
        self.user_agent = f"Mozilla/5.0 ({self.os_version_str}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{self.chrome['full']} Safari/537.36"
        self.app_version = f"5.0 ({self.os_version_str}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{self.chrome['full']} Safari/537.36"

        # Unique per-cycle Canvas noise offsets (imperceptible shifts)
        self.canvas_noise_r = round(random.uniform(-0.0003, 0.0003), 7)
        self.canvas_noise_g = round(random.uniform(-0.0003, 0.0003), 7)
        self.canvas_noise_b = round(random.uniform(-0.0003, 0.0003), 7)
        self.canvas_seed = random.randint(100000, 999999)

        # Unique per-cycle Audio noise factors
        self.audio_noise_val = round(random.uniform(0.000001, 0.00005), 8)
        self.audio_seed = random.randint(1000, 9999)

        # Media Devices UUIDs
        self.mic_id = uuid.uuid4().hex
        self.mic_group = uuid.uuid4().hex
        self.speaker_id = uuid.uuid4().hex
        self.speaker_group = uuid.uuid4().hex
        self.camera_id = uuid.uuid4().hex
        self.camera_group = uuid.uuid4().hex

        # Network Simulation
        self.downlink = round(random.uniform(8.5, 14.5), 1)
        self.rtt = random.choice([40, 50, 60, 75, 90])

        # Battery Level
        self.battery_level = round(random.uniform(0.85, 1.0), 2)
        self.battery_charging = True

    def get_summary_str(self) -> str:
        return (f"Chrome {self.chrome['full']} | {self.webgl['renderer'].split(',')[1].strip() if ',' in self.webgl['renderer'] else self.webgl['renderer']} | "
                f"{self.hardware_concurrency} Cores | {self.device_memory}GB RAM | {self.screen['width']}x{self.screen['height']} (DPR: {self.screen['dpr']}) | "
                f"{self.lang['primary']} | CanvasSeed: {self.canvas_seed}")

    def get_http_headers(self) -> dict:
        """Returns standard HTTP headers matching this fingerprint"""
        major = self.chrome["major"]
        full = self.chrome["full"]
        return {
            "User-Agent": self.user_agent,
            "Accept-Language": self.lang["accept"],
            "Sec-Ch-Ua": f'"Chromium";v="{major}", "Google Chrome";v="{major}", "Not-A.Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Ch-Ua-Platform-Version": f'"{self.os_platform_version}"',
            "Sec-Ch-Ua-Full-Version-List": f'"Chromium";v="{full}", "Google Chrome";v="{full}", "Not-A.Brand";v="99.0.0.0"',
            "Sec-Ch-Ua-Arch": '"x86"',
            "Sec-Ch-Ua-Bitness": '"64"',
            "Sec-Ch-Ua-Model": '""'
        }

    def generate_injection_script(self) -> str:
        """Generates comprehensive JavaScript injection to forge a 100% realistic browser fingerprint and bypass bot detection"""
        data = {
            "userAgent": self.user_agent,
            "appVersion": self.app_version,
            "chromeMajor": self.chrome["major"],
            "chromeFull": self.chrome["full"],
            "platform": "Win32",
            "platformVersion": self.os_platform_version,
            "languages": self.lang["languages"],
            "primaryLang": self.lang["primary"],
            "hardwareConcurrency": self.hardware_concurrency,
            "deviceMemory": self.device_memory,
            "maxTouchPoints": self.max_touch_points,
            "screenWidth": self.screen["width"],
            "screenHeight": self.screen["height"],
            "availWidth": self.screen["availWidth"],
            "availHeight": self.screen["availHeight"],
            "colorDepth": self.screen["colorDepth"],
            "devicePixelRatio": self.screen["dpr"],
            "webglVendor": self.webgl["vendor"],
            "webglRenderer": self.webgl["renderer"],
            "webglUnmaskedVendor": self.webgl["unmasked_vendor"],
            "webglUnmaskedRenderer": self.webgl["unmasked_renderer"],
            "canvasNoiseR": self.canvas_noise_r,
            "canvasNoiseG": self.canvas_noise_g,
            "canvasNoiseB": self.canvas_noise_b,
            "canvasSeed": self.canvas_seed,
            "audioNoise": self.audio_noise_val,
            "audioSeed": self.audio_seed,
            "micId": self.mic_id,
            "micGroup": self.mic_group,
            "speakerId": self.speaker_id,
            "speakerGroup": self.speaker_group,
            "cameraId": self.camera_id,
            "cameraGroup": self.camera_group,
            "downlink": self.downlink,
            "rtt": self.rtt,
            "batteryLevel": self.battery_level,
            "batteryCharging": self.battery_charging
        }

        js_data = json.dumps(data)

        script = f"""(() => {{
    const FP = {js_data};

    // Helper: make functions appear 100% native (function () {{ [native code] }})
    const makeNative = (fn, name = '') => {{
        try {{
            const fnName = name || fn.name || '';
            const toStringFn = function () {{ return `function ${{fnName}}() {{ [native code] }}`; }};
            Object.defineProperty(toStringFn, 'name', {{ value: 'toString', configurable: true }});
            Object.defineProperty(fn, 'toString', {{ value: toStringFn, configurable: true, writable: true }});
            Object.defineProperty(toStringFn, 'toString', {{ value: function () {{ return 'function toString() {{ [native code] }}'; }}, configurable: true, writable: true }});
        }} catch (e) {{}}
        return fn;
    }};

    // 1. Remove automation / CDP / Playwright / Selenium globals
    const autoGlobals = [
        '__playwright', '__pwInitScripts', '_playwrightBinding', '_pw',
        'cdc_adoQpoasnfa76pfcZLmcfl_Array', 'cdc_adoQpoasnfa76pfcZLmcfl_Promise',
        '__webdriver_evaluate', '__selenium_evaluate', '__webdriver_script_fn',
        '__driver_evaluate', 'webdriver', '__nightmare', 'callPhantom', '_phantom',
        'domAutomation', 'domAutomationController', '__fxdriver_unwrapped'
    ];
    for (const g of autoGlobals) {{
        try {{
            if (g in window) {{
                delete window[g];
            }}
        }} catch (e) {{}}
    }}

    // 2. Remove navigator.webdriver & automation flags from prototype & instance
    try {{
        delete Object.getPrototypeOf(navigator).webdriver;
    }} catch (e) {{}}
    try {{
        delete navigator.webdriver;
    }} catch (e) {{}}
    try {{
        Object.defineProperty(navigator, 'webdriver', {{
            get: makeNative(() => undefined, 'get webdriver'),
            set: makeNative(() => {{}}, 'set webdriver'),
            configurable: true,
            enumerable: true
        }});
    }} catch (e) {{}}

    // 3. Navigator Core Properties Spoofing
    const navProps = {{
        userAgent: FP.userAgent,
        appVersion: FP.appVersion,
        platform: FP.platform,
        languages: Object.freeze([...FP.languages]),
        language: FP.primaryLang,
        hardwareConcurrency: FP.hardwareConcurrency,
        deviceMemory: FP.deviceMemory,
        maxTouchPoints: FP.maxTouchPoints,
        vendor: 'Google Inc.',
        product: 'Gecko',
        productSub: '20030107',
        vendorSub: '',
        doNotTrack: null,
        pdfViewerEnabled: true
    }};

    for (const [key, val] of Object.entries(navProps)) {{
        try {{
            Object.defineProperty(navigator, key, {{
                get: makeNative(() => val, `get ${{key}}`),
                configurable: true,
                enumerable: true
            }});
        }} catch (e) {{}}
    }}

    // 4. High Entropy Navigator UserAgentData (Client Hints)
    try {{
        const userAgentData = {{
            brands: [
                {{ brand: 'Chromium', version: FP.chromeMajor }},
                {{ brand: 'Google Chrome', version: FP.chromeMajor }},
                {{ brand: 'Not-A.Brand', version: '99' }}
            ],
            mobile: false,
            platform: 'Windows',
            getHighEntropyValues: makeNative(async (hints) => ({{
                brands: [
                    {{ brand: 'Chromium', version: FP.chromeMajor }},
                    {{ brand: 'Google Chrome', version: FP.chromeMajor }},
                    {{ brand: 'Not-A.Brand', version: '99' }}
                ],
                mobile: false,
                platform: 'Windows',
                platformVersion: FP.platformVersion,
                architecture: 'x86',
                bitness: '64',
                model: '',
                wow64: false,
                fullVersionList: [
                    {{ brand: 'Chromium', version: FP.chromeFull }},
                    {{ brand: 'Google Chrome', version: FP.chromeFull }},
                    {{ brand: 'Not-A.Brand', version: '99.0.0.0' }}
                ]
            }}), 'getHighEntropyValues'),
            toJSON: makeNative(() => ({{
                brands: [
                    {{ brand: 'Chromium', version: FP.chromeMajor }},
                    {{ brand: 'Google Chrome', version: FP.chromeMajor }},
                    {{ brand: 'Not-A.Brand', version: '99' }}
                ],
                mobile: false,
                platform: 'Windows'
            }}), 'toJSON')
        }};

        Object.defineProperty(navigator, 'userAgentData', {{
            get: makeNative(() => userAgentData, 'get userAgentData'),
            configurable: true,
            enumerable: true
        }});
    }} catch (e) {{}}

    // 5. Screen & Window Geometry (Headless evasion included)
    try {{
        const screenProps = {{
            width: FP.screenWidth,
            height: FP.screenHeight,
            availWidth: FP.availWidth,
            availHeight: FP.availHeight,
            availLeft: 0,
            availTop: 0,
            colorDepth: FP.colorDepth,
            pixelDepth: FP.colorDepth,
            orientation: {{
                angle: 0,
                type: 'landscape-primary',
                onchange: null
            }}
        }};

        for (const [k, v] of Object.entries(screenProps)) {{
            try {{
                Object.defineProperty(screen, k, {{
                    get: makeNative(() => v, `get ${{k}}`),
                    configurable: true,
                    enumerable: true
                }});
            }} catch (e) {{}}
        }}

        Object.defineProperty(window, 'devicePixelRatio', {{
            get: makeNative(() => FP.devicePixelRatio, 'get devicePixelRatio'),
            configurable: true,
            enumerable: true
        }});

        // Headless window outer dimensions fix
        if (window.outerWidth === 0 || window.outerHeight === 0) {{
            Object.defineProperty(window, 'outerWidth', {{
                get: makeNative(() => window.innerWidth || FP.screenWidth, 'get outerWidth'),
                configurable: true,
                enumerable: true
            }});
            Object.defineProperty(window, 'outerHeight', {{
                get: makeNative(() => (window.innerHeight || FP.screenHeight) + 85, 'get outerHeight'),
                configurable: true,
                enumerable: true
            }});
        }}
    }} catch (e) {{}}

    // 6. Plugins & MimeTypes (Realistic 5 Plugins)
    try {{
        const makePlugin = (name, filename, desc, mimes) => {{
            const p = Object.create(Plugin.prototype);
            Object.defineProperties(p, {{
                name: {{ value: name, enumerable: true }},
                filename: {{ value: filename, enumerable: true }},
                description: {{ value: desc, enumerable: true }},
                length: {{ value: mimes.length, enumerable: true }}
            }});
            mimes.forEach((m, idx) => {{
                p[idx] = m;
                p[m.type] = m;
            }});
            return p;
        }};

        const makeMime = (type, suffixes, desc, plugin) => {{
            const m = Object.create(MimeType.prototype);
            Object.defineProperties(m, {{
                type: {{ value: type, enumerable: true }},
                suffixes: {{ value: suffixes, enumerable: true }},
                description: {{ value: desc, enumerable: true }},
                enabledPlugin: {{ value: plugin, enumerable: true }}
            }});
            return m;
        }};

        const pdfMime = makeMime('application/pdf', 'pdf', 'Portable Document Format', null);
        const textPdfMime = makeMime('text/pdf', 'pdf', 'Portable Document Format', null);
        
        const pdfPlugin = makePlugin('PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format', [pdfMime, textPdfMime]);
        const chromePdfPlugin = makePlugin('Chrome PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format', [pdfMime]);
        const chromiumPdfPlugin = makePlugin('Chromium PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format', [pdfMime]);
        const edgePdfPlugin = makePlugin('Microsoft Edge PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format', [pdfMime]);
        const webkitPdfPlugin = makePlugin('WebKit built-in PDF', 'internal-pdf-viewer', 'Portable Document Format', [pdfMime]);

        const pluginsList = [pdfPlugin, chromePdfPlugin, chromiumPdfPlugin, edgePdfPlugin, webkitPdfPlugin];
        const mimesList = [pdfMime, textPdfMime];

        const pluginArray = Object.create(PluginArray.prototype);
        Object.defineProperty(pluginArray, 'length', {{ value: pluginsList.length, enumerable: true }});
        pluginsList.forEach((pl, idx) => {{
            pluginArray[idx] = pl;
            pluginArray[pl.name] = pl;
        }});
        pluginArray[Symbol.iterator] = function* () {{
            for (let i = 0; i < pluginsList.length; i++) yield pluginsList[i];
        }};
        pluginArray.item = makeNative((i) => pluginsList[i] || null, 'item');
        pluginArray.namedItem = makeNative((n) => pluginsList.find(p => p.name === n) || null, 'namedItem');
        pluginArray.refresh = makeNative(() => {{}}, 'refresh');

        const mimeArray = Object.create(MimeTypeArray.prototype);
        Object.defineProperty(mimeArray, 'length', {{ value: mimesList.length, enumerable: true }});
        mimesList.forEach((mi, idx) => {{
            mimeArray[idx] = mi;
            mimeArray[mi.type] = mi;
        }});
        mimeArray[Symbol.iterator] = function* () {{
            for (let i = 0; i < mimesList.length; i++) yield mimesList[i];
        }};
        mimeArray.item = makeNative((i) => mimesList[i] || null, 'item');
        mimeArray.namedItem = makeNative((t) => mimesList.find(m => m.type === t) || null, 'namedItem');

        Object.defineProperty(navigator, 'plugins', {{ get: makeNative(() => pluginArray, 'get plugins'), configurable: true, enumerable: true }});
        Object.defineProperty(navigator, 'mimeTypes', {{ get: makeNative(() => mimeArray, 'get mimeTypes'), configurable: true, enumerable: true }});
    }} catch (e) {{}}

    // 7. WebGL & WebGL2 Hardware Spoofing
    try {{
        const getParameterOverride = (origFn) => makeNative(function (param) {{
            const UNMASKED_VENDOR_WEBGL = 0x9245;
            const UNMASKED_RENDERER_WEBGL = 0x9246;
            const VENDOR = 0x1F00;
            const RENDERER = 0x1F01;

            if (param === UNMASKED_VENDOR_WEBGL || param === VENDOR) {{
                return FP.webglVendor;
            }}
            if (param === UNMASKED_RENDERER_WEBGL || param === RENDERER) {{
                return FP.webglRenderer;
            }}
            if (param === 0x1F02) {{ // VERSION
                return 'WebGL 1.0 (OpenGL ES 2.0 Chromium)';
            }}
            if (param === 0x8B8C) {{ // SHADING_LANGUAGE_VERSION
                return 'WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0 Chromium)';
            }}
            return origFn.call(this, param);
        }}, 'getParameter');

        if (typeof WebGLRenderingContext !== 'undefined') {{
            const origGetParam1 = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = getParameterOverride(origGetParam1);
        }}
        if (typeof WebGL2RenderingContext !== 'undefined') {{
            const origGetParam2 = WebGL2RenderingContext.prototype.getParameter;
            WebGL2RenderingContext.prototype.getParameter = getParameterOverride(origGetParam2);
        }}
    }} catch (e) {{}}

    // 8. Subtle Canvas Noise Injection
    try {{
        const origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
        CanvasRenderingContext2D.prototype.getImageData = makeNative(function (x, y, w, h) {{
            const imgData = origGetImageData.call(this, x, y, w, h);
            const d = imgData.data;
            const step = Math.max(1, Math.floor(d.length / 500));
            for (let i = 0; i < d.length; i += step * 4) {{
                if (d[i + 3] > 0) {{
                    d[i] = Math.min(255, Math.max(0, d[i] + (i % 2 === 0 ? 1 : -1) * (FP.canvasSeed % 2)));
                    d[i + 1] = Math.min(255, Math.max(0, d[i + 1] + (i % 3 === 0 ? 1 : 0)));
                }}
            }}
            return imgData;
        }}, 'getImageData');

        const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
        HTMLCanvasElement.prototype.toDataURL = makeNative(function (type, ...args) {{
            try {{
                const ctx = this.getContext('2d');
                if (ctx && this.width > 0 && this.height > 0) {{
                    const origFill = ctx.fillStyle;
                    ctx.fillStyle = `rgba(${{(FP.canvasSeed % 3)}}, 0, 0, 0.004)`;
                    ctx.fillRect(0, 0, 1, 1);
                    ctx.fillStyle = origFill;
                }}
            }} catch (e) {{}}
            return origToDataURL.apply(this, [type, ...args]);
        }}, 'toDataURL');

        const origToBlob = HTMLCanvasElement.prototype.toBlob;
        HTMLCanvasElement.prototype.toBlob = makeNative(function (cb, type, ...args) {{
            try {{
                const ctx = this.getContext('2d');
                if (ctx && this.width > 0 && this.height > 0) {{
                    const origFill = ctx.fillStyle;
                    ctx.fillStyle = `rgba(${{(FP.canvasSeed % 3)}}, 0, 0, 0.004)`;
                    ctx.fillRect(0, 0, 1, 1);
                    ctx.fillStyle = origFill;
                }}
            }} catch (e) {{}}
            return origToBlob.apply(this, [cb, type, ...args]);
        }}, 'toBlob');
    }} catch (e) {{}}

    // 9. Subtle Audio Fingerprint Noise
    try {{
        if (typeof AudioBuffer !== 'undefined') {{
            const origGetChannelData = AudioBuffer.prototype.getChannelData;
            AudioBuffer.prototype.getChannelData = makeNative(function (ch) {{
                const data = origGetChannelData.call(this, ch);
                const noise = FP.audioNoise;
                for (let i = 0; i < data.length; i += 100) {{
                    data[i] += (i % 2 === 0 ? noise : -noise);
                }}
                return data;
            }}, 'getChannelData');
        }}

        if (typeof AnalyserNode !== 'undefined') {{
            const origGetFloatFreq = AnalyserNode.prototype.getFloatFrequencyData;
            AnalyserNode.prototype.getFloatFrequencyData = makeNative(function (arr) {{
                origGetFloatFreq.call(this, arr);
                for (let i = 0; i < arr.length; i += 32) {{
                    arr[i] += (FP.audioSeed % 5) * 0.01;
                }}
            }}, 'getFloatFrequencyData');
        }}
    }} catch (e) {{}}

    // 10. MediaDevices Enumeration Spoofing
    try {{
        if (navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {{
            navigator.mediaDevices.enumerateDevices = makeNative(async () => [
                {{
                    deviceId: FP.micId,
                    kind: 'audioinput',
                    label: 'Microphone (Realtek(R) Audio)',
                    groupId: FP.micGroup
                }},
                {{
                    deviceId: FP.speakerId,
                    kind: 'audiooutput',
                    label: 'Speakers (Realtek(R) Audio)',
                    groupId: FP.speakerGroup
                }},
                {{
                    deviceId: FP.cameraId,
                    kind: 'videoinput',
                    label: 'HD WebCam (04f2:b6d9)',
                    groupId: FP.cameraGroup
                }}
            ], 'enumerateDevices');
        }}
    }} catch (e) {{}}

    // 11. Network Information (navigator.connection)
    try {{
        const conn = {{
            downlink: FP.downlink,
            effectiveType: '4g',
            rtt: FP.rtt,
            saveData: false,
            onchange: null
        }};
        Object.defineProperty(navigator, 'connection', {{
            get: makeNative(() => conn, 'get connection'),
            configurable: true,
            enumerable: true
        }});
    }} catch (e) {{}}

    // 12. Battery API
    try {{
        navigator.getBattery = makeNative(async () => ({{
            charging: FP.batteryCharging,
            chargingTime: 0,
            dischargingTime: Infinity,
            level: FP.batteryLevel,
            addEventListener: makeNative(() => {{}}, 'addEventListener'),
            removeEventListener: makeNative(() => {{}}, 'removeEventListener')
        }}), 'getBattery');
    }} catch (e) {{}}

    // 13. Realistic window.chrome Object
    try {{
        if (!window.chrome) {{
            window.chrome = {{}};
        }}
        window.chrome.app = {{
            isInstalled: false,
            InstallState: {{ DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' }},
            RunningState: {{ CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' }},
            getDetails: makeNative(() => null, 'getDetails'),
            getIsInstalled: makeNative(() => false, 'getIsInstalled'),
            runningState: makeNative(() => 'cannot_run', 'runningState')
        }};
        window.chrome.runtime = {{
            OnInstalledReason: {{ CHROME_UPDATE: 'chrome_update', INSTALL: 'install', SHARED_MODULE_UPDATE: 'shared_module_update', UPDATE: 'update' }},
            OnRestartRequiredReason: {{ APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' }},
            PlatformArch: {{ ARM: 'arm', ARM64: 'arm64', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' }},
            PlatformNaclArch: {{ ARM: 'arm', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' }},
            PlatformOs: {{ ANDROID: 'android', CROS: 'cros', LINUX: 'linux', MAC: 'mac', OPENBSD: 'openbsd', WIN: 'win' }},
            RequestUpdateCheckStatus: {{ NO_UPDATE: 'no_update', THROTTLED: 'throttled', UPDATE_AVAILABLE: 'update_available' }},
            connect: makeNative(() => ({{ disconnect: makeNative(() => {{}}, 'disconnect'), onDisconnect: {{ addListener: makeNative(() => {{}}, 'addListener') }}, onMessage: {{ addListener: makeNative(() => {{}}, 'addListener') }}, postMessage: makeNative(() => {{}}, 'postMessage') }}), 'connect'),
            sendMessage: makeNative(() => {{}}, 'sendMessage')
        }};
        window.chrome.csi = makeNative(() => ({{ startE: Date.now(), onloadT: Date.now() + 50, pageT: 50, tran: 15 }}), 'csi');
        window.chrome.loadTimes = makeNative(() => ({{
            commitLoadTime: Date.now() / 1000,
            connectionInfo: 'h2',
            finishDocumentLoadTime: Date.now() / 1000 + 0.1,
            finishLoadTime: Date.now() / 1000 + 0.2,
            firstPaintAfterLoadTime: 0,
            firstPaintTime: Date.now() / 1000 + 0.05,
            navigationType: 'Other',
            npnNegotiatedProtocol: 'h2',
            requestTime: Date.now() / 1000 - 0.1,
            startLoadTime: Date.now() / 1000 - 0.1,
            wasAlternateProtocolAvailable: false,
            wasFetchedViaSpdy: true,
            wasNpnNegotiated: true
        }}), 'loadTimes');
    }} catch (e) {{}}

    // 14. Notification & Permissions API
    try {{
        const origQuery = navigator.permissions.query;
        navigator.permissions.query = makeNative((parameters) => {{
            if (parameters && parameters.name === 'notifications') {{
                const state = (typeof Notification !== 'undefined' && Notification.permission === 'granted') ? 'granted' : 'prompt';
                return Promise.resolve({{ state: state, onchange: null }});
            }}
            if (origQuery) return origQuery.call(navigator.permissions, parameters);
            return Promise.resolve({{ state: 'prompt', onchange: null }});
        }}, 'query');
    }} catch (e) {{}}

    // 15. Notification permission fallback in Headless
    try {{
        if (typeof Notification !== 'undefined' && Notification.permission === 'denied') {{
            Object.defineProperty(Notification, 'permission', {{
                get: makeNative(() => 'default', 'get permission'),
                configurable: true,
                enumerable: true
            }});
        }}
    }} catch (e) {{}}

}})();"""
        return script


def generate_unique_browser_fingerprint() -> BrowserFingerprint:
    """Generates a fresh, realistic browser fingerprint with unique hardware, canvas, audio, and network profiles"""
    return BrowserFingerprint()
