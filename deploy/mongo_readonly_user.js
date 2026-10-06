// Create a MongoDB user that can only read the application database.
// Usage: mongosh "<admin uri>" --eval 'const pwd = "<from your secret manager>"' deploy/mongo_readonly_user.js
db = db.getSiblingDB("policypilot");
db.createUser({ user: "policypilot_reader", pwd: pwd, roles: [{ role: "read", db: "policypilot" }] });
