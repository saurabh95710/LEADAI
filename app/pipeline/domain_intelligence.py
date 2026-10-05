"""
Domain & Keyword Intelligence Service for LeadAI.

Provides:
1. Deep Industry & Business Type Taxonomy with Grouped Keywords (Product/Service, Intent, Requirement, Location).
2. Smart Recommendation Engine tailored to Industry + Business Type + Target Customer + Offerings.
3. Organization Keyword Library Management (Suggested, Active, Custom, Excluded).
4. User Search Presets Management (Tenant Isolated).
5. Dynamic Business Context Blending for Search Runs and AI Prompts.
"""
import logging
from typing import Any, Dict, List, Optional
from bson import ObjectId

from app.db.models import utcnow

logger = logging.getLogger(__name__)

# Popular industry keys for priority card display in UI
POPULAR_INDUSTRY_KEYS = [
    "real_estate",
    "automotive",
    "education",
    "healthcare",
    "travel",
    "ecommerce",
    "software_saas",
    "events_weddings",
    "interior_design",
    "agency",
    "professional_services",
    "home_services",
]

# Universal intent terms per target customer type
TARGET_CUSTOMER_KEYWORDS = {
    "buyers": [
        "buy", "purchase", "cost", "price", "booking", "looking to buy",
        "need", "interested", "available", "deal", "discount", "budget"
    ],
    "sellers": [
        "sell", "resale", "brokerage", "valuation", "list property",
        "exchange", "trade in", "commission", "buyer available"
    ],
    "renters": [
        "rent", "to let", "lease", "monthly rent", "deposit", "pg",
        "furnished", "room for rent", "flat for rent", "immediate shifting"
    ],
    "investors": [
        "investment", "high roi", "roi", "commercial investment",
        "pre rented", "capital appreciation", "bulk deal", "rental yield"
    ],
    "students": [
        "admission", "fees", "course", "batch", "coaching", "syllabus",
        "demo class", "scholarship", "placement", "enroll"
    ],
    "patients": [
        "doctor", "appointment", "consultation", "treatment", "fees",
        "timing", "clinic", "hospital", "checkup"
    ],
    "clients": [
        "quotation", "quote", "proposal", "pricing", "hire", "service charges",
        "portfolio", "contract", "consultation", "estimate"
    ],
    "wholesale": [
        "wholesale", "bulk", "moq", "distributor", "dealership",
        "factory price", "supplier", "catalogue", "sample"
    ],
}

# Universal noise/exclusions to recommend against irrelevant comments
DEFAULT_RECOMMENDED_EXCLUSIONS = [
    "job", "jobs", "vacancy", "hiring", "salary", "fresher",
    "resume", "cv", "interview", "scam", "fraud", "fake",
    "giveaway", "free gift", "follow me", "earn money online"
]

# Structured taxonomy of Business Types and Keyword Groups per Industry
INDUSTRY_TAXONOMY: Dict[str, Dict[str, Any]] = {
    "real_estate": {
        "name": "Real Estate",
        "icon": "🏠",
        "target_customer_options": ["Buyers", "Tenants / Renters", "Investors", "Sellers", "Commercial Clients"],
        "business_types": [
            {
                "key": "residential",
                "name": "Residential Property (Flats, Villas, Houses)",
                "description": "Flats, apartments, independent villas, builder floors, and residential plots.",
                "keyword_groups": {
                    "product_service": ["apartment", "flat", "villa", "house", "builder floor", "penthouse", "plot", "land", "bungalow", "studio apartment"],
                    "intent": ["buy", "purchase", "rent", "lease", "site visit", "book", "investment", "possession"],
                    "requirement": ["1 bhk", "2 bhk", "3 bhk", "4 bhk", "ready to move", "under construction", "gated society", "furnished", "budget flat", "luxury"],
                    "location": ["prime location", "near metro", "highway", "main road", "gated community", "park facing"],
                },
            },
            {
                "key": "commercial",
                "name": "Commercial Property (Offices, Shops, Warehouses)",
                "description": "Office spaces, retail shops, showrooms, warehouses, and industrial plots.",
                "keyword_groups": {
                    "product_service": ["office space", "commercial shop", "showroom", "warehouse", "godown", "commercial land", "industrial plot", "retail space"],
                    "intent": ["lease", "rent", "buy", "investment", "high roi", "pre rented", "booking"],
                    "requirement": ["furnished office", "bare shell", "ground floor", "corner shop", "food court", "it park", "industrial"],
                    "location": ["business district", "mall", "market", "commercial hub", "highway connectivity"],
                },
            },
            {
                "key": "agency",
                "name": "Real Estate Agency / Property Brokerage",
                "description": "Brokerage firms, property consultants, and independent real estate agents.",
                "keyword_groups": {
                    "product_service": ["property", "flat", "plot", "villa", "commercial", "resale", "fresh booking", "rental"],
                    "intent": ["consultation", "buy property", "sell property", "best deal", "loan assistance", "registry", "site visit"],
                    "requirement": ["jda approved", "rera approved", "gated township", "low budget", "luxury property", "bank loan"],
                    "location": ["city area", "ring road", "developed area", "nearby amenities"],
                },
            },
            {
                "key": "builder",
                "name": "Builder / Property Developer",
                "description": "Real estate developers constructing townships, high-rises, and plotted projects.",
                "keyword_groups": {
                    "product_service": ["township", "group housing", "luxury flats", "plotted development", "villas", "commercial complex"],
                    "intent": ["pre launch offer", "booking open", "sample flat ready", "inquire now", "eoi", "launch price"],
                    "requirement": ["clubhouse", "swimming pool", "rera registered", "zero brokerage", "flexible payment plan"],
                    "location": ["expressway", "growth corridor", "smart city"],
                },
            },
            {
                "key": "rental",
                "name": "Rental & PG Services",
                "description": "Rental flats, PG accommodations, co-living spaces, and room rentals.",
                "keyword_groups": {
                    "product_service": ["flat for rent", "room for rent", "pg", "hostel", "coliving", "1 rk", "furnished flat"],
                    "intent": ["rent", "to let", "immediate shifting", "single occupancy", "sharing", "deposit"],
                    "requirement": ["fully furnished", "semi furnished", "bachelor allowed", "family only", "wifi included", "meals included"],
                    "location": ["near coaching", "near office", "walking distance", "near station"],
                },
            },
        ],
    },
    "automotive": {
        "name": "Automotive",
        "icon": "🚗",
        "target_customer_options": ["Car Buyers", "Bike Buyers", "Used Car Seekers", "Vehicle Owners (Service)", "Fleet Operators"],
        "business_types": [
            {
                "key": "used_cars",
                "name": "Used Cars / Pre-Owned Dealership",
                "description": "Certified pre-owned cars, buy/sell/exchange vehicles, and car financing.",
                "keyword_groups": {
                    "product_service": ["used car", "second hand car", "pre owned", "certified car", "suv", "sedan", "hatchback"],
                    "intent": ["buy car", "sell car", "exchange car", "test drive", "loan", "valuation", "rc transfer"],
                    "requirement": ["low km", "single owner", "automatic", "diesel", "petrol", "cng", "under warranty"],
                    "location": ["dealership", "showroom", "doorstep inspection"],
                },
            },
            {
                "key": "new_cars",
                "name": "New Car Showroom & Dealership",
                "description": "Authorized dealerships selling brand new cars, SUVs, and electric vehicles.",
                "keyword_groups": {
                    "product_service": ["new car", "suv", "sedan", "ev", "electric car", "top model", "base model"],
                    "intent": ["on road price", "booking", "test drive", "discount", "exchange bonus", "delivery date", "waiting period"],
                    "requirement": ["sunroof", "automatic", "safety rating", "mileage", "zero down payment", "low emi"],
                    "location": ["nearest showroom", "authorized dealer", "service center"],
                },
            },
            {
                "key": "two_wheeler",
                "name": "Bikes & Scooters Dealership",
                "description": "New and used motorcycles, scooters, sports bikes, and EV 2-wheelers.",
                "keyword_groups": {
                    "product_service": ["bike", "motorcycle", "scooter", "electric scooter", "ev bike", "sports bike"],
                    "intent": ["price", "test ride", "emi", "mileage", "booking", "down payment", "offers"],
                    "requirement": ["battery range", "charging time", "engine cc", "dual abs", "disc brake"],
                    "location": ["nearest showroom", "service center"],
                },
            },
            {
                "key": "auto_service",
                "name": "Car Workshop, Repair & Detailing",
                "description": "Automotive repair, body work, ceramic coating, PPF, and periodic car maintenance.",
                "keyword_groups": {
                    "product_service": ["car service", "car repair", "ceramic coating", "ppf", "dent paint", "wheel alignment", "car spa", "ac service"],
                    "intent": ["appointment", "charges", "estimate", "inspection", "doorstep service", "towing"],
                    "requirement": ["genuine parts", "insurance claim", "cashless repair", "same day delivery"],
                    "location": ["service station", "workshop nearby", "home pickup"],
                },
            },
        ],
    },
    "education": {
        "name": "Education & Coaching",
        "icon": "🎓",
        "target_customer_options": ["Students", "Parents", "Working Professionals", "Job Seekers"],
        "business_types": [
            {
                "key": "coaching",
                "name": "Coaching Institute (JEE / NEET / UPSC / SSC)",
                "description": "Competitive exam coaching, classroom batches, and test series.",
                "keyword_groups": {
                    "product_service": ["coaching", "classes", "batch", "crash course", "test series", "study material", "target batch"],
                    "intent": ["admission", "fees", "demo class", "scholarship test", "results", "faculty", "registration"],
                    "requirement": ["online batch", "offline batch", "weekend batch", "doubt sessions", "hostel facility"],
                    "location": ["nearest center", "classroom campus"],
                },
            },
            {
                "key": "higher_ed",
                "name": "College / University / Degree Programs",
                "description": "Colleges and universities offering B.Tech, MBA, BBA, BCA, and postgraduate degrees.",
                "keyword_groups": {
                    "product_service": ["btech", "mba", "bba", "bca", "mtech", "degree", "diploma", "masters", "phd"],
                    "intent": ["admission open", "eligibility", "entrance exam", "placement record", "fees structure", "apply now"],
                    "requirement": ["naac accredited", "ugc approved", "scholarship", "campus placement", "internships"],
                    "location": ["university campus", "delhi ncr", "bangalore", "mumbai", "pune"],
                },
            },
            {
                "key": "online_edtech",
                "name": "Online Courses & EdTech Bootcamps",
                "description": "Online upskilling courses, coding bootcamps, and career certifications.",
                "keyword_groups": {
                    "product_service": ["course", "bootcamp", "coding", "data science", "digital marketing", "ai course", "full stack"],
                    "intent": ["enroll now", "free masterclass", "curriculum", "placement assistance", "pricing", "emi"],
                    "requirement": ["live classes", "recorded lectures", "certificate", "1-on-1 mentorship", "capstone project"],
                    "location": ["self paced", "live online"],
                },
            },
            {
                "key": "study_abroad",
                "name": "Study Abroad & IELTS / Visa Consultant",
                "description": "Overseas education consultants, IELTS coaching, and student visa services.",
                "keyword_groups": {
                    "product_service": ["study abroad", "ielts", "toefl", "gre", "gmat", "student visa", "foreign university"],
                    "intent": ["free counseling", "visa process", "intake", "application deadline", "scholarship", "sop writing"],
                    "requirement": ["canada", "uk", "usa", "australia", "germany", "without ielts", "pr pathway"],
                    "location": ["consulate", "branch office"],
                },
            },
        ],
    },
    "healthcare": {
        "name": "Healthcare & Wellness",
        "icon": "🩺",
        "target_customer_options": ["Patients", "Families", "Health Conscious Individuals"],
        "business_types": [
            {
                "key": "clinic_hospital",
                "name": "Clinic / Multispeciality Hospital",
                "description": "Doctor clinics, day care centers, and multispeciality hospitals.",
                "keyword_groups": {
                    "product_service": ["doctor", "consultation", "treatment", "surgery", "opd", "health checkup", "icu", "emergency"],
                    "intent": ["appointment", "fees", "timing", "doctor available", "second opinion", "contact number"],
                    "requirement": ["cashless insurance", "ayushman", "specialist doctor", "experienced surgeon"],
                    "location": ["hospital address", "clinic location", "home visit"],
                },
            },
            {
                "key": "dental",
                "name": "Dental Clinic & Orthodontics",
                "description": "Dental surgeries, root canals, implants, braces, and cosmetic dentistry.",
                "keyword_groups": {
                    "product_service": ["root canal", "rct", "teeth whitening", "dental implants", "braces", "invisalign", "scaling", "tooth extraction"],
                    "intent": ["appointment", "cost", "consultation", "painless treatment", "charges"],
                    "requirement": ["digital smile", "invisible aligners", "pediatric dental", "emergency dental"],
                    "location": ["dental clinic near me"],
                },
            },
            {
                "key": "diagnostics",
                "name": "Diagnostic Center & Pathology Lab",
                "description": "Blood tests, MRI, CT scans, ultrasounds, and full body health checkups.",
                "keyword_groups": {
                    "product_service": ["blood test", "mri scan", "ct scan", "ultrasound", "full body checkup", "x ray", "pathology"],
                    "intent": ["home sample collection", "report time", "test price", "booking", "fasting required"],
                    "requirement": ["nabl accredited", "same day report", "discount package"],
                    "location": ["nearest collection center", "home visit"],
                },
            },
        ],
    },
    "software_saas": {
        "name": "Software & SaaS",
        "icon": "💻",
        "target_customer_options": ["B2B Companies", "Startups", "Enterprises", "Agencies", "Small Businesses"],
        "business_types": [
            {
                "key": "b2b_saas",
                "name": "B2B SaaS / Business Software",
                "description": "Cloud applications, CRM, ERP, automation, and workflow software.",
                "keyword_groups": {
                    "product_service": ["software", "saas", "crm", "erp", "cloud app", "automation", "dashboard", "billing software"],
                    "intent": ["request demo", "free trial", "pricing", "plans", "schedule call", "api access", "integration"],
                    "requirement": ["self hosted", "cloud hosted", "multi user", "mobile app", "white label", "security compliance"],
                    "location": ["global", "remote setup"],
                },
            },
            {
                "key": "it_services",
                "name": "Custom Software & Web Development",
                "description": "Custom mobile apps, web applications, devops, and dedicated engineering teams.",
                "keyword_groups": {
                    "product_service": ["custom software", "web development", "mobile app development", "cloud migration", "ui ux design", "devops"],
                    "intent": ["hire developers", "get quote", "hourly rate", "project cost", "portfolio", "nda"],
                    "requirement": ["react", "flutter", "python", "node", "aws", "scalable", "mvp in 30 days"],
                    "location": ["offshore", "dedicated team"],
                },
            },
        ],
    },
    "travel": {
        "name": "Travel & Tourism",
        "icon": "✈️",
        "target_customer_options": ["Vacationers", "Couples / Honeymooners", "Families", "Corporate Groups"],
        "business_types": [
            {
                "key": "tour_packages",
                "name": "Tour Operator & Holiday Packages",
                "description": "Domestic & international holiday packages, flight + hotel combos, and sightseeing.",
                "keyword_groups": {
                    "product_service": ["tour package", "holiday package", "honeymoon package", "group tour", "custom itinerary", "flights + hotel"],
                    "intent": ["price per person", "book now", "dates available", "inclusions", "itinerary details", "advance payment"],
                    "requirement": ["4 star hotel", "all meals included", "sightseeing", "visa assistance", "family friendly"],
                    "location": ["goa", "kashmir", "kerala", "himachal", "dubai", "thailand", "bali", "vietnam", "maldives"],
                },
            },
            {
                "key": "hotel_resort",
                "name": "Hotels, Resorts & Luxury Stays",
                "description": "Boutique hotels, luxury resorts, pool villas, and homestays.",
                "keyword_groups": {
                    "product_service": ["luxury room", "villa", "resort", "suite", "cottage", "pool villa", "homestay"],
                    "intent": ["room tariff", "check in", "availability", "book stay", "weekend getaway", "couple friendly"],
                    "requirement": ["swimming pool", "breakfast included", "mountain view", "beachfront", "banquet hall"],
                    "location": ["resort location", "distance from airport"],
                },
            },
        ],
    },
    "ecommerce": {
        "name": "E-commerce & Retail",
        "icon": "🛒",
        "target_customer_options": ["Online Shoppers", "Wholesale Buyers", "Retail Customers"],
        "business_types": [
            {
                "key": "fashion_d2c",
                "name": "Fashion, Apparel & D2C Brands",
                "description": "Clothing, sarees, ethnic wear, western wear, shoes, and jewelry.",
                "keyword_groups": {
                    "product_service": ["dress", "saree", "kurti", "tshirt", "jeans", "shoes", "jewellery", "handbag", "suit"],
                    "intent": ["price", "how to order", "link please", "cod available", "delivery charges", "discount code"],
                    "requirement": ["size chart", "cotton", "pure silk", "plus size", "return policy", "original product"],
                    "location": ["pan india delivery", "same day delivery"],
                },
            },
            {
                "key": "electronics_gadgets",
                "name": "Electronics & Appliances",
                "description": "Gadgets, smartphones, laptops, audio accessories, and home appliances.",
                "keyword_groups": {
                    "product_service": ["smartphone", "laptop", "smartwatch", "earbuds", "tv", "refrigerator", "ac", "air purifier"],
                    "intent": ["best price", "discount", "emi option", "warranty", "exchange offer", "specs"],
                    "requirement": ["brand warranty", "cod", "fast delivery", "bill provided"],
                    "location": ["store pickup", "doorstep delivery"],
                },
            },
        ],
    },
    "interior_design": {
        "name": "Interior Design & Furniture",
        "icon": "🛋️",
        "target_customer_options": ["Homeowners", "Commercial Owners", "Architects", "Builders"],
        "business_types": [
            {
                "key": "home_interiors",
                "name": "Residential Interior Design",
                "description": "Modular kitchens, wardrobes, false ceilings, and complete home interiors.",
                "keyword_groups": {
                    "product_service": ["modular kitchen", "wardrobe", "false ceiling", "tv unit", "wallpaper", "full home interior", "lighting"],
                    "intent": ["quote", "cost per sq ft", "site visit", "design catalog", "timeline", "estimate"],
                    "requirement": ["acrylic finish", "plywood grade", "10 year warranty", "3d design", "budget interior"],
                    "location": ["near me", "service in city"],
                },
            },
            {
                "key": "furniture_decor",
                "name": "Custom Furniture & Decor",
                "description": "Custom wooden furniture, sofa sets, dining tables, and luxury home decor.",
                "keyword_groups": {
                    "product_service": ["sofa set", "dining table", "bed with storage", "recliner", "study table", "office chair"],
                    "intent": ["price", "customization", "wood type", "fabric options", "delivery time"],
                    "requirement": ["teak wood", "sheesham", "high density foam", "warranty"],
                    "location": ["showroom address", "factory price"],
                },
            },
        ],
    },
    "events_weddings": {
        "name": "Events & Weddings",
        "icon": "💒",
        "target_customer_options": ["Brides & Grooms", "Families", "Corporate Organizers"],
        "business_types": [
            {
                "key": "wedding_planner",
                "name": "Wedding Planning & Decor",
                "description": "Destination weddings, theme decor, sound/light setups, and vendor management.",
                "keyword_groups": {
                    "product_service": ["wedding planner", "destination wedding", "mandap decor", "haldi decor", "sangeet setup", "entry theme"],
                    "intent": ["package cost", "date availability", "budget wedding", "portfolio", "consultation"],
                    "requirement": ["end to end management", "vendor coordination", "royal theme", "beach wedding"],
                    "location": ["udaipur", "jaipur", "goa", "jim corbett", "local venue"],
                },
            },
            {
                "key": "banquet_venue",
                "name": "Banquet Halls & Marriage Gardens",
                "description": "Event venues, AC banquets, marriage lawns, and catering halls.",
                "keyword_groups": {
                    "product_service": ["banquet hall", "marriage garden", "party lawn", "ac banquet", "resort for wedding"],
                    "intent": ["per plate cost", "dates open", "booking advance", "guest capacity", "visiting time"],
                    "requirement": ["veg only", "catering included", "rooms available", "parking space"],
                    "location": ["city outskirts", "central location"],
                },
            },
        ],
    },
    "agency": {
        "name": "Marketing & Creative Agencies",
        "icon": "📣",
        "target_customer_options": ["Business Owners", "E-commerce Brands", "Real Estate Companies", "Startups"],
        "business_types": [
            {
                "key": "digital_marketing",
                "name": "Digital Marketing & Performance Ads",
                "description": "Meta ads, Google ads, lead generation campaigns, and SEO.",
                "keyword_groups": {
                    "product_service": ["meta ads", "google ads", "lead generation", "seo", "social media management", "roas"],
                    "intent": ["monthly package", "cost per lead", "get proposal", "portfolio", "schedule call", "past results"],
                    "requirement": ["guaranteed leads", "ecommerce roas", "b2b leads", "influencer marketing"],
                    "location": ["pan india", "global clients"],
                },
            },
            {
                "key": "branding_creative",
                "name": "Branding, Logo & Video Production",
                "description": "Brand identity, logos, packaging design, product shoots, and commercial video ads.",
                "keyword_groups": {
                    "product_service": ["logo design", "brand identity", "packaging design", "product photoshoot", "reels production", "video ad"],
                    "intent": ["pricing", "timeline", "portfolio", "revisions", "quote"],
                    "requirement": ["vector files", "copyright transfer", "4k video", "concept presentation"],
                    "location": ["creative studio"],
                },
            },
        ],
    },
    "professional_services": {
        "name": "Professional Services",
        "icon": "⚖️",
        "target_customer_options": ["Businesses", "Founders", "Individuals", "Taxpayers"],
        "business_types": [
            {
                "key": "legal_tax",
                "name": "CA, Tax & Legal Services",
                "description": "Company registration, GST, ITR filing, trademark registration, and audits.",
                "keyword_groups": {
                    "product_service": ["company registration", "pvt ltd", "gst registration", "trademark filing", "itr filing", "audit", "fssai license"],
                    "intent": ["consultation", "government fees", "documents required", "turnaround time", "process"],
                    "requirement": ["startup india", "msme", "trademark objection", "roc compliance"],
                    "location": ["online consultation", "office visit"],
                },
            },
        ],
    },
    "home_services": {
        "name": "Home Services & Repair",
        "icon": "🔧",
        "target_customer_options": ["Homeowners", "Tenants", "Offices", "Property Managers"],
        "business_types": [
            {
                "key": "ac_appliance",
                "name": "AC & Appliance Repair",
                "description": "AC service, gas refill, washing machine repair, refrigerator servicing, and RO repair.",
                "keyword_groups": {
                    "product_service": ["ac repair", "ac installation", "gas refill", "washing machine repair", "refrigerator repair", "ro service"],
                    "intent": ["visiting charges", "book technician", "same day service", "urgently need"],
                    "requirement": ["warranty on parts", "original spare parts", "certified technician"],
                    "location": ["at home service", "near me"],
                },
            },
            {
                "key": "cleaning_pest",
                "name": "Deep Cleaning & Pest Control",
                "description": "Home deep cleaning, sofa dry cleaning, termite treatment, and general pest control.",
                "keyword_groups": {
                    "product_service": ["home deep cleaning", "sofa dry cleaning", "kitchen cleaning", "bathroom cleaning", "termite treatment", "pest control"],
                    "intent": ["cost", "appointment", "time required", "chemicals safe"],
                    "requirement": ["eco friendly chemicals", "odorless pest control", "furnished flat cleaning"],
                    "location": ["doorstep service"],
                },
            },
        ],
    },
    "general": {
        "name": "General / Other Business",
        "icon": "🌐",
        "target_customer_options": ["Customers", "Buyers", "Clients", "Inquirers"],
        "business_types": [
            {
                "key": "general_sales",
                "name": "General Products or Services",
                "description": "Any business selling products, subscriptions, or professional services.",
                "keyword_groups": {
                    "product_service": ["product", "service", "booking", "order", "package", "plan", "membership"],
                    "intent": ["price", "cost", "how to buy", "interested", "details", "contact", "available"],
                    "requirement": ["best quality", "warranty", "discount", "fast delivery", "support"],
                    "location": ["delivery available", "address", "near me"],
                },
            }
        ],
    },
}


def get_taxonomy_catalog() -> Dict[str, Any]:
    """Returns the full catalog of industries, business types, and keyword groups
    structured for UI consumption in both User and Admin portals."""
    from app.pipeline.business_context import list_industries

    builtins = {i["key"]: i for i in list_industries()}
    catalog: Dict[str, Any] = {}

    all_keys = list(dict.fromkeys(list(INDUSTRY_TAXONOMY.keys()) + list(builtins.keys())))

    for key in all_keys:
        tax = INDUSTRY_TAXONOMY.get(key) or {}
        builtin = builtins.get(key) or {}

        btype_objs = tax.get("business_types", [])
        if btype_objs:
            btypes = [bt["name"] for bt in btype_objs]
            merged_groups: Dict[str, List[str]] = {"product_service": [], "intent": [], "requirement": [], "location": []}
            for bt in btype_objs:
                for grp, words in bt.get("keyword_groups", {}).items():
                    if grp in merged_groups:
                        for w in words:
                            if w not in merged_groups[grp]:
                                merged_groups[grp].append(w)
        else:
            btypes = ["General Services", "Agency", "Specialist", "Retailer", "Consultant", "Other"]
            merged_groups = {
                "product_service": list(builtin.get("requirement_terms") or ["service", "product"])[:10],
                "intent": list(builtin.get("default_keywords") or ["price", "hire", "buy"])[:8],
                "requirement": ["price", "cost", "available", "quotation", "details"],
                "location": ["location", "near me", "contact", "address"],
            }

        name = tax.get("name") or builtin.get("name") or key.replace("_", " ").title()
        icon = tax.get("icon") or builtin.get("icon") or "🏢"
        desc = builtin.get("description") or f"Businesses and professionals in {name}."
        target_options = tax.get("target_customer_options") or ["Buyers", "Clients", "Customers", "Inquirers"]

        catalog[key] = {
            "key": key,
            "name": name,
            "icon": icon,
            "description": desc,
            "popular": key in POPULAR_INDUSTRY_KEYS,
            "business_types": btypes,
            "keyword_groups": merged_groups,
            "target_customer_options": target_options,
            "default_keywords": builtin.get("default_keywords") or [],
            "requirement_terms": builtin.get("requirement_terms") or [],
            "ai_guidance": builtin.get("ai_guidance") or "",
        }

    return catalog


def generate_recommendations(
    industry_key: Optional[str] = None,
    business_type: Optional[str] = None,
    target_customers: Optional[List[str]] = None,
    products_services: Optional[List[str]] = None,
    custom_industry: Optional[str] = None,
) -> Dict[str, Any]:
    """Generates a tailored, grouped keyword bundle based on business context.
    Returns:
      groups: { product_service: [...], intent: [...], requirement: [...], location: [...] }
      recommended: flat list of top 10-15 keywords ready for 1-click selection
      recommended_exclusions: list of keywords to avoid noise/spam
    """
    key = (industry_key or "general").strip().lower()
    tax = INDUSTRY_TAXONOMY.get(key) or INDUSTRY_TAXONOMY["general"]

    btypes = tax.get("business_types") or []
    matched_btype = None
    if business_type:
        btype_clean = business_type.strip().lower()
        matched_btype = next((b for b in btypes if b["key"] == btype_clean or b["name"].lower() == btype_clean), None)
    if not matched_btype and btypes:
        matched_btype = btypes[0]

    groups: Dict[str, List[str]] = {
        "product_service": [],
        "intent": [],
        "requirement": [],
        "location": [],
    }

    if matched_btype and matched_btype.get("keyword_groups"):
        for g_key in ("product_service", "intent", "requirement", "location"):
            groups[g_key].extend(matched_btype["keyword_groups"].get(g_key) or [])

    # Add custom products/services if supplied by user/admin
    if products_services:
        for p in products_services:
            clean_p = p.strip().lower()
            if clean_p and clean_p not in groups["product_service"]:
                groups["product_service"].insert(0, clean_p)

    # Add intent keywords based on selected target customers
    if target_customers:
        for tc in target_customers:
            tc_clean = tc.strip().lower()
            for t_key, words in TARGET_CUSTOMER_KEYWORDS.items():
                if t_key in tc_clean:
                    for w in words:
                        if w not in groups["intent"]:
                            groups["intent"].append(w)

    # If no intent keywords yet, fallback to generic
    if not groups["intent"]:
        groups["intent"] = ["price", "cost", "buy", "interested", "available", "booking", "details"]

    # Assemble recommended primary list (pick best balance from each group)
    recommended = []
    seen = set()
    def _add_rec(word):
        w = word.strip().lower()
        if w and w not in seen:
            seen.add(w)
            recommended.append(w)

    for w in groups["product_service"][:6]:
        _add_rec(w)
    for w in groups["intent"][:5]:
        _add_rec(w)
    for w in groups["requirement"][:4]:
        _add_rec(w)

    return {
        "industry": key,
        "business_type": (matched_btype.get("key") if matched_btype else business_type) or "",
        "business_type_name": (matched_btype.get("name") if matched_btype else business_type) or "",
        "groups": groups,
        "keyword_groups": groups,
        "recommended": recommended,
        "recommended_to_add": recommended,
        "recommended_exclusions": DEFAULT_RECOMMENDED_EXCLUSIONS,
        "total": len(recommended),
    }


async def get_org_keyword_library(org: Dict[str, Any], db=None) -> Dict[str, Any]:
    """Builds the comprehensive keyword library for an organization.
    Separates keywords into tabs: Suggested, Active, Custom, Excluded."""
    from app.pipeline.business_context import build_context

    ctx = build_context(org, db)
    settings = org.get("settings") or {}
    profile = settings.get("business_profile") or {}

    active_kws = list(settings.get("lead_keywords") or profile.get("active_keywords") or [])
    excluded_kws = list(settings.get("lead_exclude_keywords") or profile.get("excluded_keywords") or [])

    # Generate recommendations for this org's industry & business type
    recs = generate_recommendations(
        industry_key=ctx["industry_key"],
        business_type=profile.get("business_type"),
        target_customers=profile.get("target_customer_types") or (
            [profile["target_customers"]] if profile.get("target_customers") else None
        ),
        products_services=profile.get("primary_offerings"),
    )

    suggested_pool = recs["recommended"] + recs["groups"]["product_service"] + recs["groups"]["intent"]
    suggested_unique = []
    seen_sugg = set()
    for w in suggested_pool:
        w_low = w.lower()
        if w_low not in seen_sugg:
            seen_sugg.add(w_low)
            suggested_unique.append(w_low)

    # Active keywords list with source tracking
    active_items = []
    for k in active_kws:
        is_sugg = k.lower() in seen_sugg
        active_items.append({
            "keyword": k,
            "type": "suggested" if is_sugg else "custom",
            "status": "active"
        })

    # Custom keywords (active keywords that were not from suggestions)
    custom_items = [
        item for item in active_items if item["type"] == "custom"
    ]

    # Excluded keywords
    excluded_items = [
        {"keyword": k, "type": "excluded", "status": "excluded"}
        for k in excluded_kws
    ]

    # Suggested keywords (that are not yet active or excluded)
    suggested_items = []
    active_set = {k.lower() for k in active_kws}
    excluded_set = {k.lower() for k in excluded_kws}
    for w in suggested_unique:
        if w not in active_set and w not in excluded_set:
            suggested_items.append({
                "keyword": w,
                "type": "suggested",
                "status": "suggested"
            })

    # custom_items are a subset of active_items: listing both showed them twice
    all_keywords = active_items + suggested_items + excluded_items
    summary = {
        "active_count": len(active_items),
        "suggested_count": len(suggested_items),
        "custom_count": len(custom_items),
        "excluded_count": len(excluded_items),
        "total_count": len(all_keywords),
    }

    return {
        "success": True,
        "industry": ctx["industry_key"],
        "industry_name": ctx["industry_name"],
        "business_type": profile.get("business_type") or "",
        "target_customer_types": profile.get("target_customer_types") or [],
        "groups": recs["groups"],
        "keywords": all_keywords,
        "summary": summary,
        "counts": summary,
        "active": active_items,
        "suggested": suggested_items,
        "custom": custom_items,
        "excluded": excluded_items,
        "using_defaults": len(active_items) == 0,
        "default_exclusions": DEFAULT_RECOMMENDED_EXCLUSIONS,
    }


async def save_org_keyword_library(
    org_id: Any,
    active_keywords: List[str],
    excluded_keywords: List[str],
    db,
) -> Dict[str, Any]:
    """Persists active and excluded keywords into organization settings."""
    import inspect
    from app.pipeline.comment_filter import normalize_keyword_list

    clean_active = normalize_keyword_list(active_keywords)[:300]
    clean_excluded = normalize_keyword_list(excluded_keywords)[:300]

    try:
        oid = ObjectId(str(org_id))
    except Exception:
        oid = org_id

    res = db.organizations.update_one(
        {"_id": oid},
        {"$set": {
            "settings.lead_keywords": clean_active,
            "settings.lead_exclude_keywords": clean_excluded,
            "settings.business_profile.active_keywords": clean_active,
            "settings.business_profile.excluded_keywords": clean_excluded,
            "updated_at": utcnow(),
        }}
    )
    if inspect.isawaitable(res):
        await res

    return {
        "success": True,
        "active_keywords": clean_active,
        "excluded_keywords": clean_excluded,
        "active_count": len(clean_active),
        "excluded_count": len(clean_excluded),
    }


def merge_search_context(
    base_biz: Dict[str, Any],
    search_context: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Blends a specific search's business context (selected industry, business type,
    or keywords) into the organization's base business context."""
    if not search_context:
        return base_biz

    merged = dict(base_biz)
    ind = search_context.get("industry")
    if ind:
        merged["industry_key"] = ind.lower()
        tax = INDUSTRY_TAXONOMY.get(ind.lower())
        if tax:
            merged["industry_name"] = tax["name"]
            merged["industry"] = tax["name"]
        else:
            merged["industry_name"] = ind
            merged["industry"] = ind

    btype = search_context.get("business_type")
    if btype:
        merged["business_type"] = btype

    target_cust = search_context.get("target_customer")
    if target_cust:
        merged["target_customer"] = target_cust
        merged["target_customer_types"] = [target_cust]

    kws = search_context.get("keywords")
    if kws and isinstance(kws, list):
        # Add search keywords to requirement terms and lead_keywords so rule-based and AI recognize them
        curr_terms = list(merged.get("custom_terms") or [])
        curr_lead_kws = list(merged.get("lead_keywords") or [])
        for k in kws:
            if k and k not in curr_terms:
                curr_terms.append(k)
            if k and k not in curr_lead_kws:
                curr_lead_kws.append(k)
        merged["custom_terms"] = curr_terms
        merged["lead_keywords"] = curr_lead_kws

    return merged
