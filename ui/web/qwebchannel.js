"use strict";

var QWebChannelMessageTypes = {
    signal: 1,
    propertyUpdate: 2,
    init: 3,
    idle: 4,
    debug: 5,
    invokeMethod: 6,
    connectToSignal: 7,
    disconnectFromSignal: 8,
    setProperty: 9,
    response: 10
};

var QWebChannel = function(transport, initCallback) {
    if (typeof transport !== "object" || typeof transport.send !== "function") {
        console.error("The QWebChannel expects a transport object with a send function and onmessage callback property." +
                      " Given is: transport: " + typeof(transport) + ", transport.send: " + typeof(transport.send));
        return;
    }

    var channel = this;
    this.transport = transport;

    this.send = function(data) {
        if (typeof(data) !== "string") {
            data = JSON.stringify(data);
        }
        channel.transport.send(data);
    };

    this.transport.onmessage = function(message) {
        var data = message.data;
        if (typeof data === "string") {
            data = JSON.parse(data);
        }
        switch (data.type) {
            case QWebChannelMessageTypes.signal:
                channel.handleSignal(data);
                break;
            case QWebChannelMessageTypes.response:
                channel.handleResponse(data);
                break;
            case QWebChannelMessageTypes.propertyUpdate:
                channel.handlePropertyUpdate(data);
                break;
            default:
                console.error("invalid message received:", message.data);
                break;
        }
    };

    this.execCallbacks = {};
    this.execId = 0;
    this.exec = function(data, callback) {
        if (!callback) {
            channel.send(data);
            return;
        }
        if (channel.execId === Number.MAX_VALUE) {
            channel.execId = Number.MIN_VALUE;
        }
        if (data.hasOwnProperty("id")) {
            console.error("Cannot exec message with previous id: " + JSON.stringify(data));
            return;
        }
        data.id = channel.execId++;
        channel.execCallbacks[data.id] = callback;
        channel.send(data);
    };

    this.objects = {};

    this.handleSignal = function(message) {
        var object = channel.objects[message.object];
        if (object) {
            object.signalEmitted(message.signal, message.args);
        } else {
            console.warn("Unhandled signal: " + message.object + "::" + message.signal);
        }
    };

    this.handleResponse = function(message) {
        if (!message.hasOwnProperty("id")) {
            console.error("Invalid response message received: ", JSON.stringify(message));
            return;
        }
        var callback = channel.execCallbacks[message.id];
        if (callback) {
            delete channel.execCallbacks[message.id];
            callback(message.data);
        }
    };

    this.handlePropertyUpdate = function(message) {
        for (var i = 0; i < message.data.length; ++i) {
            var data = message.data[i];
            var object = channel.objects[data.object];
            if (object) {
                object.propertyUpdate(data.signals, data.properties);
            } else {
                console.warn("Unhandled property update: " + data.object + "::" + data.signal);
            }
        }
        channel.exec({type: QWebChannelMessageTypes.idle});
    };

    this.debug = function(message) {
        channel.send({type: QWebChannelMessageTypes.debug, data: message});
    };

    QWebChannel.prototype.unwrapQObject = function(response) {
        if (response instanceof Array) {
            return response.map(QWebChannel.prototype.unwrapQObject, this);
        }
        if (!(response instanceof Object)) {
            return response;
        }
        if (!response["__QObject*__"]) {
            return response;
        }
        var objectId = response["id"];
        if (this.objects[objectId]) {
            return this.objects[objectId];
        }
        return new QObject(objectId, response["data"], this);
    };

    this.exec({type: QWebChannelMessageTypes.init}, function(data) {
        for (var objectName in data) {
            var object = new QObject(objectName, data[objectName], channel);
        }
        for (var objectName in data) {
            channel.objects[objectName].unwrapProperties();
        }
        if (initCallback) {
            initCallback(channel);
        }
        channel.exec({type: QWebChannelMessageTypes.idle});
    });
};

function QObject(name, data, webChannel) {
    this.__id__ = name;
    webChannel.objects[name] = this;

    var object = this;
    this.__objectSignals__ = {};
    this.__propertyCache__ = {};

    var unwrapQObject = function(response) {
        return webChannel.unwrapQObject(response);
    };

    this.unwrapProperties = function() {
        for (var propertyIdx in data.properties) {
            var property = data.properties[propertyIdx];
            this[property[0]] = unwrapQObject(property[1]);
        }
    };

    this.propertyUpdate = function(signals, propertyMap) {
        for (var propertyIndex in propertyMap) {
            var propertyValue = unwrapQObject(propertyMap[propertyIndex]);
            this[propertyIndex] = propertyValue;
        }
        for (var signalName in signals) {
            var args = signals[signalName];
            this.signalEmitted(signalName, args);
        }
    };

    this.signalEmitted = function(signalName, signalArgs) {
        var unwrappedArgs = [];
        for (var i = 0; i < signalArgs.length; ++i) {
            unwrappedArgs.push(unwrapQObject(signalArgs[i]));
        }
        var handlers = this.__objectSignals__[signalName];
        if (handlers) {
            for (var idx = 0; idx < handlers.length; ++idx) {
                handlers[idx].apply(this, unwrappedArgs);
            }
        }
    };

    this.connect = function(signalName, callback) {
        if (!this.__objectSignals__[signalName]) {
            this.__objectSignals__[signalName] = [];
            webChannel.exec({
                type: QWebChannelMessageTypes.connectToSignal,
                object: this.__id__,
                signal: signalName
            });
        }
        this.__objectSignals__[signalName].push(callback);
    };

    this.disconnect = function(signalName, callback) {
        if (!this.__objectSignals__[signalName]) {
            return;
        }
        var index = this.__objectSignals__[signalName].indexOf(callback);
        if (index >= 0) {
            this.__objectSignals__[signalName].splice(index, 1);
            if (this.__objectSignals__[signalName].length === 0) {
                delete this.__objectSignals__[signalName];
                webChannel.exec({
                    type: QWebChannelMessageTypes.disconnectFromSignal,
                    object: this.__id__,
                    signal: signalName
                });
            }
        }
    };

    function createMethod(methodName) {
        return function() {
            var args = [];
            var callback;
            for (var i = 0; i < arguments.length; ++i) {
                var arg = arguments[i];
                if (typeof arg === "function") {
                    callback = arg;
                } else {
                    args.push(arg);
                }
            }
            webChannel.exec({
                type: QWebChannelMessageTypes.invokeMethod,
                object: object.__id__,
                method: methodName,
                args: args
            }, function(response) {
                if (callback) {
                    callback(unwrapQObject(response));
                }
            });
        };
    }

    for (var methodIdx in data.methods) {
        var methodName = data.methods[methodIdx][0];
        this[methodName] = createMethod(methodName);
    }

    for (var signalIdx in data.signals) {
        var signalName = data.signals[signalIdx][0];
        this[signalName] = {
            connect: (function(sig) { return function(cb) { object.connect(sig, cb); }; })(signalName),
            disconnect: (function(sig) { return function(cb) { object.disconnect(sig, cb); }; })(signalName)
        };
    }
}
