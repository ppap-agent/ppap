// Vendored @lark-opdev/block-docs-addon-api@0.0.11 for Feishu docs-addon host APIs.
// ../../node_modules/.pnpm/@bdeefe+doc-mini-app-client@1.0.22/node_modules/@bdeefe/doc-mini-app-client/dist/es/index.js
var __defProp = Object.defineProperty;
var __defProps = Object.defineProperties;
var __getOwnPropDescs = Object.getOwnPropertyDescriptors;
var __getOwnPropSymbols = Object.getOwnPropertySymbols;
var __getProtoOf = Object.getPrototypeOf;
var __hasOwnProp = Object.prototype.hasOwnProperty;
var __propIsEnum = Object.prototype.propertyIsEnumerable;
var __reflectGet = Reflect.get;
var __pow = Math.pow;
var __defNormalProp = (obj, key, value) => key in obj ? __defProp(obj, key, {
  enumerable: true,
  configurable: true,
  writable: true,
  value
}) : obj[key] = value;
var __spreadValues = (a2, b2) => {
  for (var prop in b2 || (b2 = {}))
    if (__hasOwnProp.call(b2, prop))
      __defNormalProp(a2, prop, b2[prop]);
  if (__getOwnPropSymbols)
    for (var prop of __getOwnPropSymbols(b2)) {
      if (__propIsEnum.call(b2, prop))
        __defNormalProp(a2, prop, b2[prop]);
    }
  return a2;
};
var __spreadProps = (a2, b2) => __defProps(a2, __getOwnPropDescs(b2));
var __superGet = (cls, obj, key) => __reflectGet(__getProtoOf(cls), key, obj);
var __async = (__this, __arguments, generator) => {
  return new Promise((resolve, reject) => {
    var fulfilled = (value) => {
      try {
        step(generator.next(value));
      } catch (e2) {
        reject(e2);
      }
    };
    var rejected = (value) => {
      try {
        step(generator.throw(value));
      } catch (e2) {
        reject(e2);
      }
    };
    var step = (x) => x.done ? resolve(x.value) : Promise.resolve(x.value).then(fulfilled, rejected);
    step((generator = generator.apply(__this, __arguments)).next());
  });
};
var ResponseCode;
(function(ResponseCode2) {
  ResponseCode2[ResponseCode2["SUCCESS"] = 0] = "SUCCESS";
  ResponseCode2[ResponseCode2["NOT_IMPLEMENT"] = 10110001] = "NOT_IMPLEMENT";
  ResponseCode2[ResponseCode2["REQUEST_TIMEOUT"] = 10110002] = "REQUEST_TIMEOUT";
  ResponseCode2[ResponseCode2["REQUEST_INVALID"] = 10110003] = "REQUEST_INVALID";
  ResponseCode2[ResponseCode2["RESPONSE_INVALID"] = 10110004] = "RESPONSE_INVALID";
  ResponseCode2[ResponseCode2["INTERNAL_ERROR"] = 10110005] = "INTERNAL_ERROR";
  ResponseCode2[ResponseCode2["FORBIDDEN"] = 10110006] = "FORBIDDEN";
  ResponseCode2[ResponseCode2["TOO_MANY_REQUEST"] = 10110007] = "TOO_MANY_REQUEST";
  ResponseCode2[ResponseCode2["BAD_REQUEST"] = 10110008] = "BAD_REQUEST";
})(ResponseCode || (ResponseCode = {}));
var ResponseMessage = {
  [ResponseCode.SUCCESS]: "Success",
  [ResponseCode.NOT_IMPLEMENT]: "API not implemented",
  [ResponseCode.REQUEST_TIMEOUT]: "Request timeout",
  [ResponseCode.REQUEST_INVALID]: "Invalid request",
  [ResponseCode.RESPONSE_INVALID]: "Invalid response",
  [ResponseCode.INTERNAL_ERROR]: "Internal error",
  [ResponseCode.FORBIDDEN]: "Request forbidden",
  [ResponseCode.TOO_MANY_REQUEST]: "To many request",
  [ResponseCode.BAD_REQUEST]: "Bad request"
};
var API_STATUS;
(function(API_STATUS2) {
  API_STATUS2["SUCCESS"] = "ok";
  API_STATUS2["FAIL"] = "fail";
})(API_STATUS || (API_STATUS = {}));
var FUNCTION_ID_PREFIX = "__$_FUNCTION_ID_$__";
var FUNCTION_ID_KEY = "__$_FUNCTION_ID_KEY_$__";
var INVOKE_FUNCTION_TYPE = "__$_INVOKE_FUNCTION_$__";
function paramArrayToParamObject(params) {
  const paramObj = {};
  params.forEach((param, i2) => {
    paramObj[i2] = param;
  });
  return paramObj;
}
function paramObjectToParamArray(paramObj) {
  const maxParamsCount = 10;
  const params = [];
  for (let i2 = 0; i2 < maxParamsCount; i2++) {
    params.push(paramObj[i2]);
  }
  return params;
}
function generateFunctionId() {
  const RANDOM_NUMBER = 31;
  const id = Math.floor(Math.random() * __pow(2, RANDOM_NUMBER));
  return `${FUNCTION_ID_PREFIX}_${id}`;
}
function isFunctionId(id) {
  return typeof id === "string" && id.startsWith(FUNCTION_ID_PREFIX);
}
var APIInvokeError = class extends Error {
  constructor(code, scopes, originMsg = "", stack) {
    super(`[${code}] ${scopes.join(".")} - ${originMsg || ResponseMessage[code] || ""}`);
    this.code = code;
    this.scopes = scopes;
    this.originMsg = originMsg;
    this.name = "APIInvokeError";
    if (stack) {
      this.stack = stack;
    }
  }
};
function isAPIInvokeError(e2) {
  return e2 instanceof Error && e2.name === "APIInvokeError";
}
function isAPIRequest(request) {
  return Boolean(request) && Boolean(request.id) && Boolean(request.body) && Array.isArray(request.body.scopes) && Array.isArray(request.body.params);
}
function isAPIResponse(response) {
  return Boolean(response) && Boolean(response.id) && Boolean(response.body) && typeof response.body.code === "number" && typeof response.body.msg === "string";
}
function getAPIKey(scopes) {
  return scopes.join("_");
}
var Host = class {
  constructor() {
    this.middleware = [];
    this.composeMiddleware = (_ctx, next) => __async(this, null, function* () {
      return yield next();
    });
    this.APIMap = /* @__PURE__ */ new Map();
    this.invokeAPI = (ctx) => __async(this, null, function* () {
      return this.composeMiddleware(ctx, () => __async(this, null, function* () {
        const { scopes, params } = ctx.reqData;
        const APIKey = getAPIKey(scopes);
        const API = this.APIMap.get(APIKey);
        if (API.handler) {
          ctx.resData.body = yield API.handler.call(null, ctx.source, ...params);
        }
      }));
    });
  }
  compose(middleware) {
    if (!Array.isArray(middleware)) {
      throw new TypeError("Middleware stack must be an array!");
    }
    for (const fn of middleware) {
      if (typeof fn !== "function") {
        throw new TypeError("Middleware must be composed of functions!");
      }
    }
    return function(ctx, next) {
      let index = -1;
      return dispatch(0);
      function dispatch(i2) {
        if (i2 <= index) {
          return Promise.reject(new Error("next() called multiple times"));
        }
        index = i2;
        let fn = middleware[i2];
        if (i2 === middleware.length) {
          fn = next;
        }
        if (!fn) {
          return Promise.resolve();
        }
        try {
          return Promise.resolve(fn(ctx, dispatch.bind(null, i2 + 1)));
        } catch (err) {
          return Promise.reject(err);
        }
      }
    };
  }
  createContext(reqData, source, postDuration) {
    return {
      reqData,
      resData: {
        body: null,
        postDuration
      },
      source
    };
  }
  getApi(apiKey) {
    return this.APIMap.get(apiKey);
  }
  use(fn) {
    if (typeof fn !== "function") {
      throw new TypeError("middleware must be a function!");
    }
    this.middleware.push(fn);
    this.composeMiddleware = this.compose(this.middleware);
    return this;
  }
  registerAPI(scopes, handler) {
    const APIKey = getAPIKey(scopes);
    if (this.APIMap.has(APIKey)) {
      throw new Error(`Duplicated API registered: ${APIKey}`);
    }
    this.APIMap.set(APIKey, {
      scopes,
      handler
    });
  }
};
var __assign = function() {
  __assign = Object.assign || function __assign2(t2) {
    for (var s2, i2 = 1, n2 = arguments.length; i2 < n2; i2++) {
      s2 = arguments[i2];
      for (var p2 in s2)
        if (Object.prototype.hasOwnProperty.call(s2, p2))
          t2[p2] = s2[p2];
    }
    return t2;
  };
  return __assign.apply(this, arguments);
};
function __rest(s2, e2) {
  var t2 = {};
  for (var p2 in s2)
    if (Object.prototype.hasOwnProperty.call(s2, p2) && e2.indexOf(p2) < 0)
      t2[p2] = s2[p2];
  if (s2 != null && typeof Object.getOwnPropertySymbols === "function")
    for (var i2 = 0, p2 = Object.getOwnPropertySymbols(s2); i2 < p2.length; i2++) {
      if (e2.indexOf(p2[i2]) < 0 && Object.prototype.propertyIsEnumerable.call(s2, p2[i2]))
        t2[p2[i2]] = s2[p2[i2]];
    }
  return t2;
}
function __awaiter(thisArg, _arguments, P, generator) {
  function adopt(value) {
    return value instanceof P ? value : new P(function(resolve) {
      resolve(value);
    });
  }
  return new (P || (P = Promise))(function(resolve, reject) {
    function fulfilled(value) {
      try {
        step(generator.next(value));
      } catch (e2) {
        reject(e2);
      }
    }
    function rejected(value) {
      try {
        step(generator["throw"](value));
      } catch (e2) {
        reject(e2);
      }
    }
    function step(result) {
      result.done ? resolve(result.value) : adopt(result.value).then(fulfilled, rejected);
    }
    step((generator = generator.apply(thisArg, _arguments || [])).next());
  });
}
function __generator(thisArg, body) {
  var _2 = {
    label: 0,
    sent: function() {
      if (t2[0] & 1)
        throw t2[1];
      return t2[1];
    },
    trys: [],
    ops: []
  }, f2, y2, t2, g2;
  return g2 = {
    next: verb(0),
    "throw": verb(1),
    "return": verb(2)
  }, typeof Symbol === "function" && (g2[Symbol.iterator] = function() {
    return this;
  }), g2;
  function verb(n2) {
    return function(v2) {
      return step([
        n2,
        v2
      ]);
    };
  }
  function step(op) {
    if (f2)
      throw new TypeError("Generator is already executing.");
    while (g2 && (g2 = 0, op[0] && (_2 = 0)), _2)
      try {
        if (f2 = 1, y2 && (t2 = op[0] & 2 ? y2["return"] : op[0] ? y2["throw"] || ((t2 = y2["return"]) && t2.call(y2), 0) : y2.next) && !(t2 = t2.call(y2, op[1])).done)
          return t2;
        if (y2 = 0, t2)
          op = [
            op[0] & 2,
            t2.value
          ];
        switch (op[0]) {
          case 0:
          case 1:
            t2 = op;
            break;
          case 4:
            _2.label++;
            return {
              value: op[1],
              done: false
            };
          case 5:
            _2.label++;
            y2 = op[1];
            op = [
              0
            ];
            continue;
          case 7:
            op = _2.ops.pop();
            _2.trys.pop();
            continue;
          default:
            if (!(t2 = _2.trys, t2 = t2.length > 0 && t2[t2.length - 1]) && (op[0] === 6 || op[0] === 2)) {
              _2 = 0;
              continue;
            }
            if (op[0] === 3 && (!t2 || op[1] > t2[0] && op[1] < t2[3])) {
              _2.label = op[1];
              break;
            }
            if (op[0] === 6 && _2.label < t2[1]) {
              _2.label = t2[1];
              t2 = op;
              break;
            }
            if (t2 && _2.label < t2[2]) {
              _2.label = t2[2];
              _2.ops.push(op);
              break;
            }
            if (t2[2])
              _2.ops.pop();
            _2.trys.pop();
            continue;
        }
        op = body.call(thisArg, _2);
      } catch (e2) {
        op = [
          6,
          e2
        ];
        y2 = 0;
      } finally {
        f2 = t2 = 0;
      }
    if (op[0] & 5)
      throw op[1];
    return {
      value: op[0] ? op[1] : void 0,
      done: true
    };
  }
}
function __values(o2) {
  var s2 = typeof Symbol === "function" && Symbol.iterator, m2 = s2 && o2[s2], i2 = 0;
  if (m2)
    return m2.call(o2);
  if (o2 && typeof o2.length === "number")
    return {
      next: function() {
        if (o2 && i2 >= o2.length)
          o2 = void 0;
        return {
          value: o2 && o2[i2++],
          done: !o2
        };
      }
    };
  throw new TypeError(s2 ? "Object is not iterable." : "Symbol.iterator is not defined.");
}
function __read(o2, n2) {
  var m2 = typeof Symbol === "function" && o2[Symbol.iterator];
  if (!m2)
    return o2;
  var i2 = m2.call(o2), r2, ar = [], e2;
  try {
    while ((n2 === void 0 || n2-- > 0) && !(r2 = i2.next()).done)
      ar.push(r2.value);
  } catch (error) {
    e2 = {
      error
    };
  } finally {
    try {
      if (r2 && !r2.done && (m2 = i2["return"]))
        m2.call(i2);
    } finally {
      if (e2)
        throw e2.error;
    }
  }
  return ar;
}
function __spreadArray(to, from, pack) {
  if (pack || arguments.length === 2)
    for (var i2 = 0, l2 = from.length, ar; i2 < l2; i2++) {
      if (ar || !(i2 in from)) {
        if (!ar)
          ar = Array.prototype.slice.call(from, 0, i2);
        ar[i2] = from[i2];
      }
    }
  return to.concat(ar || Array.prototype.slice.call(from));
}
var Runtime = function() {
  function Runtime2() {
  }
  Runtime2.prototype.onInvokeAPI = function(func) {
    if (func && this.invokeAPIFunc) {
      console.warn("duplicated runtime.onApiInvoke");
    }
    this.invokeAPIFunc = func;
  };
  Runtime2.prototype.makeAPI = function(scope, config) {
    var _this = this;
    if (config === void 0) {
      config = {};
    }
    var scopes = [
      scope
    ];
    var invoke = function() {
      var params = [];
      for (var _i = 0; _i < arguments.length; _i++) {
        params[_i] = arguments[_i];
      }
      return __awaiter(_this, void 0, void 0, function() {
        return __generator(this, function(_a) {
          if (!this.invokeAPIFunc) {
            throw new Error("missing runtime");
          }
          return [
            2,
            this.invokeAPIFunc({
              params,
              scopes,
              config
            })
          ];
        });
      });
    };
    invoke.__is_api_func__ = true;
    invoke.__scopes = scopes;
    return invoke;
  };
  Runtime2.prototype.makeScope = function(scope, apiMap) {
    var iterate = function(apiMap2) {
      Object.values(apiMap2).forEach(function(apiFunc) {
        if (isAPIFunc(apiFunc)) {
          var scopes = apiFunc.__scopes || [];
          scopes.unshift(scope);
          apiFunc.__scopes = scopes;
        } else if (typeof apiFunc === "object") {
          iterate(apiFunc);
        }
      });
      return apiMap2;
    };
    return iterate(apiMap);
  };
  return Runtime2;
}();
var runtime = new Runtime();
function isAPIFunc(apiFunc) {
  return typeof apiFunc === "function" && Boolean(apiFunc.__is_api_func__);
}
var TextBlock = {
  getRawText,
  getPlainText,
  addTextStyle,
  removeTextStyle
};
var ButtonActionType;
(function(ButtonActionType2) {
  ButtonActionType2["Default"] = "Default";
  ButtonActionType2["DuplicatePage"] = "DuplicatePage";
  ButtonActionType2["FollowPage"] = "FollowPage";
  ButtonActionType2["OpenLink"] = "OpenLink";
  ButtonActionType2["OpenDocVerse"] = "OpenDocVerse";
})(ButtonActionType || (ButtonActionType = {}));
function getRawText(data) {
  return data.text.elements.reduce(function(text, element) {
    if ("text_run" in element) {
      return text + element.text_run.content;
    } else {
      return "".concat(text, " ");
    }
  }, "");
}
function getPlainText(data) {
  if (data.plain_text) {
    return data.plain_text;
  }
  return data.text.elements.reduce(function(text, element) {
    if ("text_run" in element) {
      return text + element.text_run.content;
    } else if ("mention_user" in element) {
      return "".concat(text, "@").concat(element.mention_user.user_name);
    } else if ("mention_doc" in element) {
      return "".concat(text, "@").concat(element.mention_doc.title);
    } else if ("equation" in element) {
      return "".concat(text).concat(element.equation.content);
    } else if ("file" in element) {
      return "".concat(text).concat(element.file.name);
    }
    return text;
  }, "");
}
function addTextStyle(data, style, range) {
  var e_1, _a;
  if (range && range[0] === range[1]) {
    return data;
  }
  var text = data.text, reset = __rest(data, [
    "text"
  ]);
  if (!text.elements.length) {
    return data;
  }
  var index = 0;
  var elements = [];
  try {
    for (var _b = __values(text.elements), _c = _b.next(); !_c.done; _c = _b.next()) {
      var element = _c.value;
      if ("text_run" in element) {
        var content = element.text_run.content;
        var formatRange = !range ? [
          0,
          content.length
        ] : range.map(function(n2) {
          return n2 - index;
        });
        var textRange = [
          0,
          content.length
        ];
        var rangeIntersect = !(formatRange[0] > textRange[1] || formatRange[1] < textRange[0]);
        if (rangeIntersect) {
          var a2 = content.slice(textRange[0], Math.max(textRange[0], formatRange[0]));
          var b2 = content.slice(Math.max(textRange[0], formatRange[0]), Math.min(textRange[1], formatRange[1]));
          var c2 = content.slice(Math.min(textRange[1], formatRange[1]), textRange[1]);
          [
            {
              content: a2,
              style: element.text_run.style
            },
            {
              content: b2,
              style: __assign(__assign({}, element.text_run.style), style)
            },
            {
              content: c2,
              style: element.text_run.style
            }
          ].forEach(function(text_run) {
            if (text_run.content.length > 0) {
              elements.push({
                text_run
              });
            }
          });
        } else {
          elements.push(element);
        }
        index = index + content.length;
      } else if ("mention_user" in element || "mention_doc" in element || "reminder" in element || "file" in element || "inline_block" in element) {
        var formatRange = !range ? [
          0,
          1
        ] : range.map(function(n2) {
          return n2 - index;
        });
        var textRange = [
          0,
          1
        ];
        var rangeIntersect = !(formatRange[0] > textRange[1] || formatRange[1] < textRange[0]);
        if (rangeIntersect) {
          elements.push(__assign(__assign({}, element), {
            style: __assign(__assign({}, element.style), style)
          }));
        } else {
          elements.push(element);
        }
      } else {
        elements.push(element);
        index = index + 1;
      }
    }
  } catch (e_1_1) {
    e_1 = {
      error: e_1_1
    };
  } finally {
    try {
      if (_c && !_c.done && (_a = _b.return))
        _a.call(_b);
    } finally {
      if (e_1)
        throw e_1.error;
    }
  }
  return __assign({
    text: {
      elements
    }
  }, reset);
}
function removeTextStyle(data, range) {
  var emptyStyle = {
    bold: void 0,
    italic: void 0,
    underline: void 0,
    strikethrough: void 0,
    inline_code: void 0,
    background_color: void 0,
    text_color: void 0,
    link: void 0
  };
  return addTextStyle(data, emptyStyle, range);
}
var getTextBlockRawText = TextBlock.getRawText;
var getTextBlockPlainText = TextBlock.getPlainText;
var addTextBlockTextStyle = TextBlock.addTextStyle;
var removeTextBlockTextStyle = TextBlock.removeTextStyle;
var HighlightTextColor;
(function(HighlightTextColor2) {
  HighlightTextColor2["RED_500"] = "R500";
  HighlightTextColor2["ORANG_500"] = "O500";
  HighlightTextColor2["YELLOW_500"] = "Y500";
  HighlightTextColor2["GREEN_500"] = "G500";
  HighlightTextColor2["BLUE_500"] = "B500";
  HighlightTextColor2["PURPLE_500"] = "P500";
  HighlightTextColor2["RED_500_TINT"] = "R500Tint";
  HighlightTextColor2["ORANG_500_TINT"] = "O500Tint";
  HighlightTextColor2["YELLOW_500_TINT"] = "Y500Tint";
  HighlightTextColor2["GREEN_500_TINT"] = "G500Tint";
  HighlightTextColor2["BLUE_500_TINT"] = "B500Tint";
  HighlightTextColor2["PURPLE_500_TINT"] = "P500Tint";
})(HighlightTextColor || (HighlightTextColor = {}));
var TextualBlock = {
  getRawText: function(data) {
    return getTextBlockRawText(data);
  },
  getPlainText: function(data) {
    return getTextBlockPlainText(data);
  },
  addTextStyle: function(data, style, range) {
    return addTextBlockTextStyle(data, style, range);
  },
  removeTextStyle: function(data, range) {
    return removeTextBlockTextStyle(data, range);
  },
  highlightTexts: runtime.makeAPI("highlightTexts"),
  clearAllHighlightTexts: runtime.makeAPI("clearAllHighlightTexts")
};
var ImageBlock = {
  insertByUploadImage: runtime.makeAPI("insertByUploadImage", {
    timeout: false
  }),
  getImageData: runtime.makeAPI("getImageData")
};
var TableBlock = {
  insertByRowsAndCols: runtime.makeAPI("insertByRowsAndCols"),
  insertRows: runtime.makeAPI("insertRows"),
  removeRows: runtime.makeAPI("removeRows"),
  insertCols: runtime.makeAPI("insertCols"),
  removeCols: runtime.makeAPI("removeCols")
};
var Block = {
  getBlock: runtime.makeAPI("getBlock"),
  getBlocks: runtime.makeAPI("getBlocks"),
  insertBlock: runtime.makeAPI("insertBlock"),
  appendBlock: runtime.makeAPI("appendBlock"),
  removeBlock: runtime.makeAPI("removeBlock"),
  removeBlocks: runtime.makeAPI("removeBlocks"),
  updateBlock: runtime.makeAPI("updateBlock"),
  updateBlocks: runtime.makeAPI("updateBlocks"),
  updateBlockByOptions: runtime.makeAPI("updateBlockByOptions"),
  updateBlocksByOptions: runtime.makeAPI("updateBlocksByOptions"),
  turnIntoBlock: runtime.makeAPI("turnIntoBlock"),
  turnIntoBlocks: runtime.makeAPI("turnIntoBlocks"),
  showBlockMenu: runtime.makeAPI("showBlockMenu"),
  TextBlock: runtime.makeScope("TextBlock", TextBlock),
  TextualBlock: runtime.makeScope("TextualBlock", TextualBlock),
  ImageBlock: runtime.makeScope("ImageBlock", ImageBlock),
  TableBlock: runtime.makeScope("TableBlock", TableBlock)
};
var BlockType;
(function(BlockType2) {
  BlockType2["PAGE"] = "page";
  BlockType2["BITABLE"] = "bitable";
  BlockType2["CALLOUT"] = "callout";
  BlockType2["CHAT_CARD"] = "chat_card";
  BlockType2["CODE"] = "code";
  BlockType2["DIVIDER"] = "divider";
  BlockType2["DIAGRAM"] = "diagram";
  BlockType2["FILE"] = "file";
  BlockType2["GRID"] = "grid";
  BlockType2["GRID_COLUMN"] = "grid_column";
  BlockType2["HEADING1"] = "heading1";
  BlockType2["HEADING2"] = "heading2";
  BlockType2["HEADING3"] = "heading3";
  BlockType2["HEADING4"] = "heading4";
  BlockType2["HEADING5"] = "heading5";
  BlockType2["HEADING6"] = "heading6";
  BlockType2["HEADING7"] = "heading7";
  BlockType2["HEADING8"] = "heading8";
  BlockType2["HEADING9"] = "heading9";
  BlockType2["IFRAME"] = "iframe";
  BlockType2["IMAGE"] = "image";
  BlockType2["ISV"] = "isv";
  BlockType2["MINDNOTE"] = "mindnote";
  BlockType2["BULLET"] = "bullet";
  BlockType2["ORDERED"] = "ordered";
  BlockType2["TODO"] = "todo";
  BlockType2["QUOTE"] = "quote";
  BlockType2["QUOTE_CONTAINER"] = "quote_container";
  BlockType2["SHEET"] = "sheet";
  BlockType2["TABLE"] = "table";
  BlockType2["CELL"] = "table_cell";
  BlockType2["TEXT"] = "text";
  BlockType2["VIEW"] = "view";
  BlockType2["TASK"] = "task";
  BlockType2["SYNCED_SOURCE"] = "synced_source";
  BlockType2["SYNCED_REFERENCE"] = "synced_reference";
  BlockType2["WHITEBOARD"] = "whiteboard";
})(BlockType || (BlockType = {}));
var IFrameBlockType;
(function(IFrameBlockType2) {
  IFrameBlockType2["xigua"] = "xigua";
  IFrameBlockType2["youtube"] = "youtube";
  IFrameBlockType2["bilibili"] = "bilibili";
  IFrameBlockType2["bilibiliBv"] = "bilibili-bv";
  IFrameBlockType2["bilibiliBp"] = "bilibili_bp";
  IFrameBlockType2["bilibilip"] = "bilibilip";
  IFrameBlockType2["youku"] = "youku";
  IFrameBlockType2["airtable"] = "airtable";
  IFrameBlockType2["baidumap"] = "baidumap";
  IFrameBlockType2["autonavi"] = "autonavi";
  IFrameBlockType2["gmaps"] = "gmaps";
  IFrameBlockType2["tiktok"] = "tiktok";
  IFrameBlockType2["figma"] = "figma";
  IFrameBlockType2["datastudio"] = "datastudio";
  IFrameBlockType2["ad"] = "ad";
  IFrameBlockType2["says"] = "says";
  IFrameBlockType2["survey"] = "survey";
  IFrameBlockType2["trello"] = "trello";
  IFrameBlockType2["vimeo"] = "vimeo";
  IFrameBlockType2["imgur"] = "imgur";
  IFrameBlockType2["speakerdeck"] = "speakerdeck";
  IFrameBlockType2["instagram"] = "instagram";
  IFrameBlockType2["facebook"] = "facebook";
  IFrameBlockType2["googleDrive"] = "googleDrive";
  IFrameBlockType2["oneDrive"] = "oneDrive";
  IFrameBlockType2["openStreetMap"] = "openStreetMap";
  IFrameBlockType2["marvelapp"] = "marvelapp";
  IFrameBlockType2["prezi"] = "prezi";
  IFrameBlockType2["jinshuju"] = "jinshuju";
  IFrameBlockType2["soundcloud"] = "soundcloud";
  IFrameBlockType2["spotify"] = "spotify";
  IFrameBlockType2["pastebin"] = "pastebin";
  IFrameBlockType2["lucidchart"] = "lucidchart";
  IFrameBlockType2["drawIO"] = "drawIO";
  IFrameBlockType2["modao"] = "modao";
  IFrameBlockType2["canva"] = "canva";
  IFrameBlockType2["slideshare"] = "slideshare";
  IFrameBlockType2["haikudeck"] = "haikudeck";
  IFrameBlockType2["upscribe"] = "upscribe";
  IFrameBlockType2["wenjuan"] = "wenjuan";
  IFrameBlockType2["indify"] = "indify";
  IFrameBlockType2["jcode"] = "jcode";
  IFrameBlockType2["codepen"] = "codepen";
  IFrameBlockType2["tiktokCode"] = "tiktokCode";
  IFrameBlockType2["trelloCode"] = "trelloCode";
  IFrameBlockType2["vimeoCode"] = "vimeoCode";
  IFrameBlockType2["instagramCode"] = "instagramCode";
  IFrameBlockType2["facebookCode"] = "facebookCode";
  IFrameBlockType2["googleDriveCode"] = "googleDriveCode";
  IFrameBlockType2["datastudioCode"] = "datastudioCode";
  IFrameBlockType2["openstreetmapCode"] = "openstreetmapCode";
  IFrameBlockType2["figmaCode"] = "figmaCode";
  IFrameBlockType2["soundcloudCode"] = "soundcloudCode";
  IFrameBlockType2["pastebinCode"] = "pastebinCode";
  IFrameBlockType2["lucidchartCode"] = "lucidchartCode";
  IFrameBlockType2["drawIOCode"] = "drawIOCode";
  IFrameBlockType2["slideshareCode"] = "slideshareCode";
  IFrameBlockType2["speakerdeckCode"] = "speakerdeckCode";
  IFrameBlockType2["haikudeckCode"] = "haikudeckCode";
  IFrameBlockType2["feishu_survey"] = "feishu_survey";
  IFrameBlockType2["lark_survey"] = "lark_survey";
  IFrameBlockType2["tea_bulletin"] = "tea_bulletin";
  IFrameBlockType2["tea_bulletin_suffix"] = "tea_bulletin_suffix";
  IFrameBlockType2["tea_report"] = "tea_report";
  IFrameBlockType2["aeolus"] = "aeolus";
})(IFrameBlockType || (IFrameBlockType = {}));
var TaskBlockStatus;
(function(TaskBlockStatus2) {
  TaskBlockStatus2[TaskBlockStatus2["New"] = 0] = "New";
  TaskBlockStatus2[TaskBlockStatus2["Initializing"] = 1] = "Initializing";
  TaskBlockStatus2[TaskBlockStatus2["Initialized"] = 2] = "Initialized";
  TaskBlockStatus2[TaskBlockStatus2["Failed"] = 3] = "Failed";
})(TaskBlockStatus || (TaskBlockStatus = {}));
var MILLISECOND$3 = 1e3;
var SECOND$3 = 30;
var Document = {
  getPageMeta: runtime.makeAPI("getPageMeta", {
    timeout: MILLISECOND$3 * SECOND$3
  }),
  getRootBlock: runtime.makeAPI("getRootBlock"),
  getTitle: runtime.makeAPI("getTitle"),
  setTitle: runtime.makeAPI("setTitle"),
  insertBlocksByHTML: runtime.makeAPI("insertBlocksByHTML"),
  getIsUnderVersionIndex: runtime.makeAPI("getIsUnderVersionIndex"),
  insertBlocksByMarkdown: runtime.makeAPI("insertBlocksByMarkdown"),
  getDocAsMarkdown: runtime.makeAPI("getDocAsMarkdown"),
  getDocAsPlainText: runtime.makeAPI("getDocAsPlainText")
};
var Fullscreen = {
  enterFullscreen: runtime.makeAPI("enterFullscreen"),
  exitFullscreen: runtime.makeAPI("exitFullscreen")
};
var Preview = {
  previewBlockImage: runtime.makeAPI("previewBlockImage"),
  previewImage: runtime.makeAPI("previewImage"),
  uploadImage: runtime.makeAPI("uploadImage"),
  fetchImage: runtime.makeAPI("fetchImage"),
  downloadImage: runtime.makeAPI("downloadImage")
};
var History = {
  getMajorVersions: runtime.makeAPI("getMajorVersions"),
  getMinorVersions: runtime.makeAPI("getMinorVersions")
};
var Permission = {
  getDocumentPermission: runtime.makeAPI("getDocumentPermission"),
  onDocumentPermissionChange: runtime.makeAPI("onDocumentPermissionChange"),
  offDocumentPermissionChange: runtime.makeAPI("offDocumentPermissionChange")
};
var LOGIN_TIME_OUT = 1e3 * 30;
var User = {
  getUserId: runtime.makeAPI("getUserId"),
  getUserInfo: runtime.makeAPI("getUserInfo"),
  login: runtime.makeAPI("login", {
    timeout: LOGIN_TIME_OUT
  })
};
var Fold = {
  toggleFold: runtime.makeAPI("toggleFold"),
  getFoldStateMap: runtime.makeAPI("getFoldStateMap")
};
var FloatCard = {
  enterFloatCard: runtime.makeAPI("enterFloatCard"),
  exitFloatCard: runtime.makeAPI("exitFloatCard"),
  onFloatCardStateChange: runtime.makeAPI("onFloatCardStateChange"),
  offFloatCardStateChange: runtime.makeAPI("offFloatCardStateChange")
};
var Hyperlink = {
  open: runtime.makeAPI("open")
};
var Window = {
  getLocation: runtime.makeAPI("getLocation")
};
var Service = {
  History: runtime.makeScope("History", History),
  Preview: runtime.makeScope("Preview", Preview),
  Fullscreen: runtime.makeScope("Fullscreen", Fullscreen),
  Permission: runtime.makeScope("Permission", Permission),
  User: runtime.makeScope("User", User),
  Fold: runtime.makeScope("Fold", Fold),
  FloatCard: runtime.makeScope("FloatCard", FloatCard),
  Hyperlink: runtime.makeScope("Hyperlink", Hyperlink),
  Window: runtime.makeScope("Window", Window)
};
var Selection = {
  getSelectedBlocks: runtime.makeAPI("getSelectedBlocks"),
  setBlockSelection: runtime.makeAPI("setBlockSelection"),
  getSelection: runtime.makeAPI("getSelection"),
  getSelectionAsMarkdown: runtime.makeAPI("getSelectionAsMarkdown"),
  getSelectionAsPlainText: runtime.makeAPI("getSelectionAsPlainText"),
  setSelection: runtime.makeAPI("setSelection"),
  clearSelection: runtime.makeAPI("clearSelection"),
  onSelectionChange: runtime.makeAPI("onSelectionChange"),
  offSelectionChange: runtime.makeAPI("offSelectionChange")
};
var MILLISECOND$2 = 1e3;
var SECOND$2 = 30;
var Bridge = {
  getInitData: runtime.makeAPI("getInitData"),
  updateHeight: runtime.makeAPI("updateHeight"),
  updateResize: runtime.makeAPI("updateResize"),
  updatePosition: runtime.makeAPI("updatePosition"),
  updateVisiable: runtime.makeAPI("updateVisiable"),
  getContainerRect: runtime.makeAPI("getContainerRect"),
  setData: runtime.makeAPI("setData", {
    timeout: MILLISECOND$2 * SECOND$2
  }),
  getData: runtime.makeAPI("getData", {
    timeout: MILLISECOND$2 * SECOND$2
  }),
  onDataChange: runtime.makeAPI("onDataChange"),
  offDataChange: runtime.makeAPI("offDataChange")
};
var MILLISECOND$1 = 1e3;
var SECOND$1 = 30;
var Record = {
  getRecord: runtime.makeAPI("getRecord"),
  setRecord: runtime.makeAPI("setRecord", {
    timeout: MILLISECOND$1 * SECOND$1
  }),
  onRecordChange: runtime.makeAPI("onRecordChange"),
  offRecordChange: runtime.makeAPI("offRecordChange"),
  applyTransaction: function(handler) {
    return __awaiter(void 0, void 0, void 0, function() {
      var changesets;
      return __generator(this, function(_a) {
        changesets = [];
        handler({
          remove: function(path) {
            changesets.push({
              type: "remove",
              data: {
                path
              }
            });
          },
          insert: function(path, value) {
            changesets.push({
              type: "insert",
              data: {
                path,
                value
              }
            });
          },
          replace: function(path, value) {
            changesets.push({
              type: "replace",
              data: {
                path,
                value
              }
            });
          }
        });
        if (changesets.length === 0) {
          return [
            2,
            Record.getRecord()
          ];
        }
        return [
          2,
          Record.setRecord(changesets)
        ];
      });
    });
  }
};
var MILLISECOND = 1e3;
var SECOND = 30;
var Interaction = {
  getData: runtime.makeAPI("getData", {
    timeout: MILLISECOND * SECOND
  }),
  setData: runtime.makeAPI("setData", {
    timeout: MILLISECOND * SECOND
  }),
  onDataChange: runtime.makeAPI("onDataChange", {
    timeout: MILLISECOND * SECOND
  }),
  offDataChange: runtime.makeAPI("offDataChange", {
    timeout: MILLISECOND * SECOND
  }),
  applyTransaction: runtime.makeAPI("applyTransaction", {
    timeout: MILLISECOND * SECOND
  })
};
var InteractionChangesetType;
(function(InteractionChangesetType2) {
  InteractionChangesetType2["REMOVE"] = "remove";
  InteractionChangesetType2["REPLACE"] = "replace";
  InteractionChangesetType2["MOVE"] = "move";
})(InteractionChangesetType || (InteractionChangesetType = {}));
var ChatType;
(function(ChatType2) {
  ChatType2["P2P"] = "P2P";
  ChatType2["GROUP"] = "GROUP";
  ChatType2["TOPIC_GROUP"] = "TOPIC_GROUP";
  ChatType2["UNKNOWN"] = "unknown";
})(ChatType || (ChatType = {}));
var ViewAction = {
  showMessage: runtime.makeAPI("showMessage", {
    timeout: false
  }),
  hideMessage: runtime.makeAPI("hideMessage"),
  showChooseChat: runtime.makeAPI("showChooseChat", {
    timeout: false
  }),
  showChooseContact: runtime.makeAPI("showChooseContact", {
    timeout: false
  }),
  showUserProfile: runtime.makeAPI("showUserProfile"),
  hideUserProfile: runtime.makeAPI("hideUserProfile"),
  openModal: runtime.makeAPI("openModal", {
    timeout: false
  }),
  closeModal: runtime.makeAPI("closeModal"),
  showPopup: runtime.makeAPI("showPopup", {
    timeout: false
  }),
  hidePopup: runtime.makeAPI("hidePopup"),
  requestFullscreen: runtime.makeAPI("requestFullscreen"),
  exitFullscreen: runtime.makeAPI("exitFullscreen"),
  closeTopbar: runtime.makeAPI("closeTopbar")
};
var View = {
  Action: runtime.makeScope("Action", ViewAction)
};
var Viewport = {
  scrollToTop: runtime.makeAPI("scrollToTop"),
  scrollToBlock: runtime.makeAPI("scrollToBlock"),
  onViewportChange: runtime.makeAPI("onViewportChange"),
  offViewportChange: runtime.makeAPI("offViewportChange"),
  getViewportBlocks: runtime.makeAPI("getViewportBlocks")
};
var InlineComponentTriggerType;
(function(InlineComponentTriggerType2) {
  InlineComponentTriggerType2["Click"] = "click";
  InlineComponentTriggerType2["Dblclick"] = "dblclick";
  InlineComponentTriggerType2["Enter"] = "enter";
  InlineComponentTriggerType2["Leave"] = "leave";
  InlineComponentTriggerType2["Longpress"] = "longpress";
})(InlineComponentTriggerType || (InlineComponentTriggerType = {}));
var Events = {
  onDocumentChange: runtime.makeAPI("onDocumentChange"),
  offDocumentChange: runtime.makeAPI("offDocumentChange"),
  onBlockHoverChange: runtime.makeAPI("onBlockHoverChange"),
  offBlockHoverChange: runtime.makeAPI("offBlockHoverChange"),
  onInlineComponentTriggerChange: runtime.makeAPI("onInlineComponentTriggerChange"),
  offInlineComponentTriggerChange: runtime.makeAPI("offInlineComponentTriggerChange")
};
var DarkMode = {
  getIsDarkMode: runtime.makeAPI("getIsDarkMode"),
  onDarkModeChange: runtime.makeAPI("onDarkModeChange"),
  offDarkModeChange: runtime.makeAPI("offDarkModeChange")
};
var DOCS_MODE;
(function(DOCS_MODE2) {
  DOCS_MODE2["READING"] = "READING";
  DOCS_MODE2["EDITING"] = "EDITING";
  DOCS_MODE2["NONE"] = "NONE";
  DOCS_MODE2["UNKNOWN"] = "UNKNOWN";
})(DOCS_MODE || (DOCS_MODE = {}));
var DocsMode = {
  getDocsMode: runtime.makeAPI("getDocsMode"),
  onDocsModeChange: runtime.makeAPI("onDocsModeChange"),
  offDocsModeChange: runtime.makeAPI("offDocsModeChange")
};
var Language = {
  getLanguage: runtime.makeAPI("getLanguage")
};
var PRESENTATION_MODE;
(function(PRESENTATION_MODE2) {
  PRESENTATION_MODE2["NONE"] = "none";
  PRESENTATION_MODE2["DOCUMENT"] = "document";
  PRESENTATION_MODE2["PAGINATION"] = "pagination";
})(PRESENTATION_MODE || (PRESENTATION_MODE = {}));
var PresentationMode = {
  getPresentationMode: runtime.makeAPI("getPresentationMode"),
  isInPresentationMode: runtime.makeAPI("isInPresentationMode")
};
var Cookie = {
  securityCookieDetect: runtime.makeAPI("securityCookieDetect")
};
var Version = {
  getVersionInfo: runtime.makeAPI("getVersionInfo")
};
var Env = {
  DarkMode: runtime.makeScope("DarkMode", DarkMode),
  DocsMode: runtime.makeScope("DocsMode", DocsMode),
  Language: runtime.makeScope("Language", Language),
  PresentationMode: runtime.makeScope("PresentationMode", PresentationMode),
  Cookie: runtime.makeScope("Cookie", Cookie),
  Version: runtime.makeScope("Version", Version)
};
var LifeCycle = {
  notifyAppReady: runtime.makeAPI("notifyAppReady"),
  notifyPdfExportReady: runtime.makeAPI("notifyPdfExportReady"),
  onFirstCreated: runtime.makeAPI("onFirstCreated"),
  offFirstCreated: runtime.makeAPI("offFirstCreated")
};
var Comment = {
  addNewComment: runtime.makeAPI("addNewComment"),
  removeComment: runtime.makeAPI("removeComment"),
  activeComment: runtime.makeAPI("activeComment"),
  deactiveComment: runtime.makeAPI("deactiveComment"),
  onAddedComment: runtime.makeAPI("onAddedComment"),
  onRemoveTempComment: runtime.makeAPI("onRemoveTempComment"),
  onResolveComment: runtime.makeAPI("onResolveComment"),
  onReopenComment: runtime.makeAPI("onReopenComment"),
  onDeleteComment: runtime.makeAPI("onDeleteComment"),
  onActiveComment: runtime.makeAPI("onActiveComment"),
  onDeactiveComment: runtime.makeAPI("onDeactiveComment"),
  onHoverComment: runtime.makeAPI("onHoverComment"),
  onDeHoverComment: runtime.makeAPI("onDeHoverComment"),
  onSetCommentData: runtime.makeAPI("onSetCommentData"),
  commentReady: runtime.makeAPI("commentReady"),
  updatePosition: runtime.makeAPI("updatePosition"),
  commentEnable: runtime.makeAPI("commentEnable")
};
var RevisionType;
(function(RevisionType2) {
  RevisionType2["ADD"] = "add";
  RevisionType2["DELETE"] = "delete";
  RevisionType2["REPLACE"] = "replace";
  RevisionType2["FORMAT"] = "format";
})(RevisionType || (RevisionType = {}));
var Revision = {
  applyRevisions: runtime.makeAPI("applyRevisions")
};
var RootContainer;
(function(RootContainer2) {
  RootContainer2["PageMain"] = "PageMain";
  RootContainer2["SyncedReference"] = "SyncedReference";
})(RootContainer || (RootContainer = {}));
var DocMiniApp = {
  canIUse: runtime.makeAPI("canIUse"),
  getActiveDocumentRef: runtime.makeAPI("getActiveDocumentRef"),
  getActiveBlockRef: runtime.makeAPI("getActiveBlockRef"),
  getActiveBlock: runtime.makeAPI("getActiveBlock"),
  getActiveWikiToken: runtime.makeAPI("getActiveWikiToken"),
  getActiveRootContainer: runtime.makeAPI("getActiveRootContainer"),
  getDocumentRefById: function(docToken) {
    return {
      docToken
    };
  },
  getBlockRefById: function(docRef, blockId) {
    return {
      docRef,
      blockId
    };
  },
  getBlockRefByIndex: function(parentRef, index) {
    return {
      docRef: parentRef.docRef,
      parentRef,
      index,
      blockId: 0
    };
  },
  getTextRefByRange: function(blockRef, range) {
    return __assign(__assign({}, blockRef), {
      range
    });
  },
  Document: runtime.makeScope("Document", Document),
  Selection: runtime.makeScope("Selection", Selection),
  Block: runtime.makeScope("Block", Block),
  Service: runtime.makeScope("Service", Service),
  Bridge: runtime.makeScope("Bridge", Bridge),
  Events: runtime.makeScope("Events", Events),
  Interaction: runtime.makeScope("Interaction", Interaction),
  Record: runtime.makeScope("Record", Record),
  Viewport: runtime.makeScope("Viewport", Viewport),
  View: runtime.makeScope("View", View),
  Env: runtime.makeScope("Env", Env),
  LifeCycle: runtime.makeScope("LifeCycle", LifeCycle),
  Comment: runtime.makeScope("Comment", Comment),
  Revision: runtime.makeScope("Revision", Revision)
};
function getAllAPIKeys(keysToString) {
  if (keysToString === void 0) {
    keysToString = function(keys) {
      return keys.join("_");
    };
  }
  var APIKeys = [];
  var iterate = function(apiObject, keys) {
    if (keys === void 0) {
      keys = [];
    }
    Object.keys(apiObject).forEach(function(key) {
      var value = apiObject[key];
      var currentKeys = __spreadArray(__spreadArray([], __read(keys), false), [
        key
      ], false);
      if (isAPIFunc(value)) {
        APIKeys.push(keysToString(currentKeys));
      } else {
        iterate(value, currentKeys);
      }
    });
  };
  iterate(DocMiniApp);
  return APIKeys;
}
var readablePermission = {
  readable: true
};
var editablePermission = {
  editable: true
};
var blockSchema = {
  Block_getBlock: readablePermission,
  Block_getBlocks: readablePermission,
  Block_insertBlock: editablePermission,
  Block_appendBlock: editablePermission,
  Block_removeBlock: editablePermission,
  Block_removeBlocks: editablePermission,
  Block_updateBlock: editablePermission,
  Block_updateBlocks: editablePermission,
  Block_turnIntoBlock: editablePermission,
  Block_turnIntoBlocks: editablePermission,
  Block_TextBlock_getRawText: {},
  Block_TextBlock_getPlainText: {},
  Block_TextBlock_addTextStyle: {},
  Block_TextBlock_removeTextStyle: {},
  Block_TextualBlock_getRawText: {},
  Block_TextualBlock_getPlainText: {},
  Block_TextualBlock_addTextStyle: {},
  Block_TextualBlock_removeTextStyle: {},
  Block_TextualBlock_highlightTexts: readablePermission,
  Block_TextualBlock_clearAllHighlightTexts: readablePermission,
  Block_ImageBlock_insertByUploadImage: editablePermission,
  Block_ImageBlock_getImageData: readablePermission,
  Block_TableBlock_insertByRowsAndCols: editablePermission,
  Block_TableBlock_insertRows: editablePermission,
  Block_TableBlock_removeRows: editablePermission,
  Block_TableBlock_insertCols: editablePermission,
  Block_TableBlock_removeCols: editablePermission
};
var bridgeSchema = {
  Bridge_getInitData: readablePermission,
  Bridge_updateHeight: readablePermission,
  Bridge_getContainerRect: readablePermission,
  Bridge_setData: readablePermission,
  Bridge_getData: readablePermission,
  Bridge_onDataChange: readablePermission,
  Bridge_offDataChange: readablePermission,
  Bridge_updateResize: readablePermission
};
var documentSchema = {
  Document_getPageMeta: readablePermission,
  Document_getRootBlock: readablePermission,
  Document_getTitle: readablePermission,
  Document_setTitle: editablePermission,
  Document_insertBlocksByHTML: editablePermission,
  Document_getIsUnderVersionIndex: readablePermission
};
var envSchema = {
  Env_DarkMode_getIsDarkMode: readablePermission,
  Env_DarkMode_onDarkModeChange: readablePermission,
  Env_DarkMode_offDarkModeChange: readablePermission,
  Env_DocsMode_getDocsMode: readablePermission,
  Env_DocsMode_onDocsModeChange: readablePermission,
  Env_DocsMode_offDocsModeChange: readablePermission,
  Env_Language_getLanguage: readablePermission,
  Env_PresentationMode_getPresentationMode: readablePermission,
  Env_PresentationMode_isInPresentationMode: readablePermission
};
var eventsSchema = {
  Events_onDocumentChange: readablePermission,
  Events_offDocumentChange: readablePermission,
  Events_onBlockHoverChange: readablePermission,
  Events_offBlockHoverChange: readablePermission
};
var interactionSchema = {
  Interaction_getData: readablePermission,
  Interaction_setData: readablePermission,
  Interaction_onDataChange: readablePermission,
  Interaction_offDataChange: readablePermission,
  Interaction_applyTransaction: readablePermission
};
var lifeCycleSchema = {
  LifeCycle_notifyAppReady: readablePermission,
  LifeCycle_onFirstCreated: readablePermission,
  LifeCycle_offFirstCreated: readablePermission
};
var recordSchema = {
  Record_getRecord: readablePermission,
  Record_setRecord: editablePermission,
  Record_applyTransaction: editablePermission,
  Record_onRecordChange: readablePermission,
  Record_offRecordChange: readablePermission
};
var rootSchema = {
  canIUse: {},
  getActiveDocumentRef: readablePermission,
  getActiveBlockRef: readablePermission,
  getActiveBlock: readablePermission,
  getDocumentRefById: {},
  getBlockRefById: {},
  getBlockRefByIndex: {},
  getTextRefByRange: {},
  getActiveWikiToken: readablePermission
};
var selectionSchema = {
  Selection_getSelectedBlocks: readablePermission,
  Selection_getSelection: readablePermission,
  Selection_setSelection: readablePermission,
  Selection_setBlockSelection: readablePermission,
  Selection_clearSelection: readablePermission,
  Selection_onSelectionChange: readablePermission,
  Selection_offSelectionChange: readablePermission
};
var serviceSchema = {
  Service_History_getMajorVersions: editablePermission,
  Service_History_getMinorVersions: editablePermission,
  Service_Preview_previewBlockImage: readablePermission,
  Service_Preview_previewImage: readablePermission,
  Service_Fullscreen_enterFullscreen: readablePermission,
  Service_Fullscreen_exitFullscreen: readablePermission,
  Service_Permission_getDocumentPermission: readablePermission,
  Service_Permission_onDocumentPermissionChange: readablePermission,
  Service_Permission_offDocumentPermissionChange: readablePermission,
  Service_User_getUserId: readablePermission,
  Service_User_getUserInfo: readablePermission,
  Service_User_login: readablePermission,
  Service_Fold_toggleFold: readablePermission,
  Service_Fold_getFoldStateMap: readablePermission,
  Service_FloatCard_enterFloatCard: readablePermission,
  Service_FloatCard_exitFloatCard: readablePermission,
  Service_FloatCard_onFloatCardStateChange: readablePermission,
  Service_FloatCard_offFloatCardStateChange: readablePermission,
  Service_Hyperlink_open: readablePermission,
  Service_Window_getLocation: readablePermission,
  Service_Preview_uploadImage: readablePermission,
  Service_Preview_fetchImage: readablePermission
};
var viewActionSchema = {
  View_Action_showMessage: readablePermission,
  View_Action_hideMessage: readablePermission,
  View_Action_showChooseChat: readablePermission,
  View_Action_showChooseContact: readablePermission,
  View_Action_showUserProfile: readablePermission,
  View_Action_hideUserProfile: readablePermission,
  View_Action_openModal: readablePermission,
  View_Action_closeModal: readablePermission,
  View_Action_showPopup: readablePermission,
  View_Action_hidePopup: readablePermission
};
var viewportSchema = {
  Viewport_scrollToTop: readablePermission,
  Viewport_scrollToBlock: readablePermission,
  Viewport_onViewportChange: readablePermission,
  Viewport_offViewportChange: readablePermission,
  Viewport_getViewportBlocks: readablePermission
};
var apiPermissionSchema = __assign(__assign(__assign(__assign(__assign(__assign(__assign(__assign(__assign(__assign(__assign(__assign(__assign({}, blockSchema), bridgeSchema), documentSchema), envSchema), eventsSchema), interactionSchema), lifeCycleSchema), recordSchema), rootSchema), selectionSchema), serviceSchema), viewActionSchema), viewportSchema);
function e(e2, t2, n2) {
  return t2 in e2 ? Object.defineProperty(e2, t2, {
    value: n2,
    enumerable: true,
    configurable: true,
    writable: true
  }) : e2[t2] = n2, e2;
}
var t = "blockit-iframe-transferable";
var n = "blockit-iframe-transferable-proxy";
var i = Symbol(n);
var s = "__transferable_call__";
var r = "__free_transferable__";
var a = "__free_transferable_proxy__";
var o = class {
  constructor(t2) {
    this.bridge = t2, e(this, "uid", 0), e(this, "link", /* @__PURE__ */ new Map()), e(this, "reverseLink", /* @__PURE__ */ new Map()), e(this, "proxyLink", /* @__PURE__ */ new Map()), e(this, "proxyReverseLink", /* @__PURE__ */ new Map());
  }
  createObjectProxy(e2) {
    const { __id__: t2, __type__: r2 } = e2;
    let a2 = this.proxyLink.get(t2);
    return a2 || ("function" === r2 && (a2 = new Proxy(() => {
    }, {
      apply: (e3, n2, i2) => {
        if (!this.proxyLink.has(t2))
          throw new Error("Transferable object is freed");
        return this.bridge.callMethod(s, [
          t2,
          i2
        ]);
      },
      get: (e3, t3, s2) => t3 === i ? n : Reflect.get(e3, t3, s2)
    })), this.proxyLink.set(t2, a2), this.proxyReverseLink.set(a2, t2)), a2;
  }
  freeTransferableObject(e2) {
    this.isTransferableObjectProxy(e2) ? this.freeObjectProxy(e2) : this.freeObject(e2);
  }
  freeObjectProxy(e2) {
    const t2 = this.internalFreeObjectProxy(e2);
    t2 && this.bridge.emitEvent(r, [
      t2
    ]);
  }
  freeObject(e2) {
    const t2 = this.internalFreeObject(e2);
    t2 && this.bridge.emitEvent(a, [
      t2
    ]);
  }
  createObject(e2) {
    if (this.reverseLink.has(e2))
      return {
        __sign__: t,
        __type__: typeof e2,
        __id__: this.reverseLink.get(e2)
      };
    const n2 = ++this.uid;
    return this.link.set(n2, e2), this.reverseLink.set(e2, n2), {
      __sign__: t,
      __type__: typeof e2,
      __id__: n2
    };
  }
  isTransferableObject(e2) {
    return "object" == typeof e2 && !!e2 && e2.__sign__ === t && "string" == typeof e2.__type__ && "number" == typeof e2.__id__;
  }
  isTransferableObjectProxy(e2) {
    return !!e2 && e2[i] === n;
  }
  isApiName(e2) {
    return e2 === s;
  }
  isEventName(e2) {
    return e2 === r || e2 === a;
  }
  methodHandler(e2, t2) {
    if (e2 === s) {
      const [e1, n2] = t2, i2 = this.link.get(e1);
      if ("function" == typeof i2)
        return i2(...n2);
    } else if (e2 === r) {
      const [e22] = t2;
      this.internalFreeObjectId(e22);
    } else if (e2 === a) {
      const [e3] = t2;
      this.internalFreeObjectProxyId(e3);
    }
  }
  internalFreeObject(e2) {
    const t2 = this.reverseLink.get(e2);
    return t2 ? (this.reverseLink.delete(e2), this.link.delete(t2), t2) : 0;
  }
  internalFreeObjectId(e2) {
    const t2 = this.link.get(e2);
    return t2 ? (this.link.delete(e2), this.reverseLink.delete(t2), t2) : null;
  }
  internalFreeObjectProxy(e2) {
    const t2 = this.proxyReverseLink.get(e2);
    return t2 ? (this.proxyReverseLink.delete(e2), this.proxyLink.delete(t2), t2) : 0;
  }
  internalFreeObjectProxyId(e2) {
    const t2 = this.proxyLink.get(e2);
    return t2 ? (this.proxyReverseLink.delete(t2), this.proxyLink.delete(e2), t2) : null;
  }
};
var l = class {
  constructor() {
    e(this, "cloneable", []);
  }
  isHydrate(e2) {
    return e2 instanceof ArrayBuffer || e2 instanceof Blob || e2 instanceof File || e2 instanceof FileList || e2 instanceof Map || e2 instanceof Set || e2 instanceof RegExp;
  }
  dehydrate(e2) {
    const { cloneable: t2 } = this;
    let n2 = t2.indexOf(e2);
    -1 === n2 && (n2 = t2.length, t2.push(e2));
    return {
      __sign__: "blockit-iframe-dehydrate",
      __index__: n2
    };
  }
  dehydrates() {
    return this.cloneable;
  }
};
var h = class {
  constructor(e2 = []) {
    this.transfer = e2;
  }
  isDehydrate(e2) {
    return !!e2 && "blockit-iframe-dehydrate" === e2.__sign__;
  }
  hydrate(e2) {
    return this.transfer[e2.__index__];
  }
};
var c = class {
  static invokeHandler(e2, t2, n2) {
    let i2 = e2;
    for (; i2 && !i2.invokeMap.has(t2); )
      i2 = i2.parent;
    if (i2) {
      return i2.invokeMap.get(t2)(...n2);
    }
    throw new Error(`Cannot found handler of ${t2}`);
  }
  static eventHandler(e2, t2, n2) {
    let i2 = e2;
    for (; i2; ) {
      var s2;
      const e1 = null !== (s2 = i2.eventMap.get(t2)) && void 0 !== s2 ? s2 : [];
      for (const t1 of e1)
        t1(...n2);
      i2 = i2.parent;
    }
  }
  constructor(t2) {
    this.parent = t2, e(this, "invokeMap", /* @__PURE__ */ new Map()), e(this, "eventMap", /* @__PURE__ */ new Map());
  }
  registerApi(e2, t2) {
    this.invokeMap.has(e2) || this.invokeMap.set(e2, t2);
  }
  subscribeEvent(e2, t2) {
    var n2;
    const i2 = null !== (n2 = this.eventMap.get(e2)) && void 0 !== n2 ? n2 : [];
    i2.push(t2), this.eventMap.set(e2, i2);
  }
  unsubscribeEvent(e2, t2) {
    var n2;
    const i2 = null !== (n2 = this.eventMap.get(e2)) && void 0 !== n2 ? n2 : [], s2 = i2.indexOf(t2);
    -1 !== s2 && i2.splice(s2, 1);
  }
};
function d(e2, t2, n2, i2, s2) {
  return {
    name: n2,
    type: t2,
    data: i2,
    target: e2,
    _postDuration: s2
  };
}
var u = class extends class {
  constructor(t2) {
    this.postMessage = t2, e(this, "children", {});
  }
  handleMessage(e2, t2) {
    const n2 = this.children[e2.channel];
    n2 && n2.handleMessage(e2, t2);
  }
  linkChild(e2) {
    this.children[e2.channel] = e2;
  }
  unlinkChild(e2) {
    delete this.children[e2.channel];
  }
  post(e2, t2, n2, i2) {
    var s2;
    const r2 = {
      signature: "iframe-block",
      type: e2,
      payload: t2,
      channel: i2.channel,
      timestamp: Date.now(),
      dehydrates: n2
    };
    this.postMessage(i2.target, r2, null !== (s2 = i2.targetOrigin) && void 0 !== s2 ? s2 : "*");
  }
} {
};
var p = class extends class {
  constructor(t2, n2, i2) {
    this.channel = t2, this.parent = n2, this.originChecker = i2, e(this, "transferable", new o(this)), e(this, "callMap", /* @__PURE__ */ new Map()), e(this, "callId", 0), n2.linkChild(this);
  }
  callMethod(e2, t2, n2) {
    const i2 = ++this.callId;
    return new Promise((s2, r2) => {
      var a2;
      this.callMap.set(i2, s2), this.sendRequest(i2, e2, t2, null !== (a2 = null == n2 ? void 0 : n2.transfer) && void 0 !== a2 ? a2 : []);
    }).then((e3) => {
      if (this.callMap.delete(i2), e3.success)
        return e3.data;
      throw function([e4, t3]) {
        if (e4) {
          const t1 = new Error(e4.message);
          return t1.name = e4.name, t1.stack = e4.stack, t1;
        }
        return t3;
      }(e3.data);
    });
  }
  emitEvent(e2, t2) {
    this.send("event", {
      name: e2,
      data: t2
    }, []);
  }
  getSendOptions() {
    return {
      channel: this.channel
    };
  }
  handleMessage(e2, t2) {
    if (!this.originChecker(t2))
      return;
    const { transferable: n2 } = this, i2 = new h(e2.dehydrates), s2 = JSON.parse(e2.payload, (e3, t3) => this.transferable.isTransferableObject(t3) ? n2.createObjectProxy(t3) : i2.isDehydrate(t3) ? i2.hydrate(t3) : t3);
    "request" === e2.type ? this.handleRequest(e2, s2) : "response" === e2.type ? this.handleResponse(e2, s2) : "event" === e2.type && this.handleEvent(e2, s2);
  }
  handleRequest(e2, t2) {
    const { name: n2, data: i2, id: s2 } = t2;
    new Promise((t3, s3) => {
      t3(this.methodHandler(n2, i2, Date.now() - e2.timestamp));
    }).then((e3) => {
      this.sendResponse(s2, true, e3);
    }, (e3) => {
      var t3;
      this.sendResponse(s2, false, (t3 = e3) instanceof Error ? [
        {
          name: t3.name,
          message: t3.message,
          stack: t3.stack
        },
        null
      ] : [
        null,
        t3
      ]);
    });
  }
  handleResponse(e2, t2) {
    const { id: n2 } = t2, i2 = this.callMap.get(n2);
    null == i2 || i2(t2), this.callMap.delete(n2);
  }
  handleEvent(e2, t2) {
    this.eventHandler(t2.name, t2.data, Date.now() - e2.timestamp);
  }
  sendRequest(e2, t2, n2, i2) {
    const s2 = {
      id: e2,
      name: t2,
      data: n2
    };
    this.send("request", s2, i2);
  }
  sendResponse(e2, t2, n2) {
    const i2 = {
      id: e2,
      success: t2,
      data: n2
    };
    this.send("response", i2, []);
  }
  send(e2, t2, n2) {
    const { transferable: i2 } = this, s2 = new l(), r2 = JSON.stringify(t2, (e3, t3) => "function" == typeof t3 && -1 !== n2.indexOf(t3) ? i2.createObject(t3) : s2.isHydrate(t3) ? s2.dehydrate(t3) : t3);
    this.parent.post(e2, r2, s2.dehydrates(), this.getSendOptions());
  }
  destroy() {
    this.parent.unlinkChild(this);
  }
} {
  constructor(t2, n2, i2, s2) {
    super(t2, i2, (e2) => function(e3, t3) {
      if (t3) {
        if ("string" == typeof t3)
          return -1 !== e3.indexOf(t3);
        if (t3 instanceof RegExp)
          return null !== e3.match(t3);
        if ("function" == typeof t3)
          return t3(e3);
      }
      return true;
    }(e2, s2.options.allowedOrigin)), this.origin = n2, this.bridge = i2, this.client = s2, e(this, "apiStore", new c(null));
  }
  eventHandler(e2, t2, n2) {
    if (this.transferable.isEventName(e2))
      return this.transferable.methodHandler(e2, t2);
    c.eventHandler(this.apiStore, e2, [
      d(null, "publish", e2, t2, n2)
    ]);
  }
  methodHandler(e2, t2, n2) {
    return this.transferable.isApiName(e2) ? this.transferable.methodHandler(e2, t2) : c.invokeHandler(this.apiStore, e2, [
      d(null, "invoke", e2, t2, n2)
    ]);
  }
  getSendOptions() {
    return __spreadProps(__spreadValues({}, super.getSendOptions()), {
      target: window.parent,
      targetOrigin: this.origin
    });
  }
};
var f = class {
  constructor(t2, n2, i2) {
    var s2;
    this.blockTypeId = t2, this.channel = n2, this.options = i2, e(this, "bridge", void 0), e(this, "instanceBridge", void 0), this.bridge = new u((e2, t3, n3, i3) => {
      window.parent.postMessage(t3, n3);
    }), this.instanceBridge = new p(n2, null !== (s2 = i2.targetOrigin) && void 0 !== s2 ? s2 : "*", this.bridge, this);
  }
  registerApi(e2, t2) {
    this.instanceBridge.apiStore.registerApi(e2, t2);
  }
  registerApis(e2) {
    const t2 = Object.keys(e2);
    for (let n2 = 0; n2 < t2.length; n2++)
      this.registerApi(t2[n2], e2[t2[n2]]);
  }
  subscribeEvent(e2, t2) {
    this.instanceBridge.apiStore.subscribeEvent(e2, t2);
  }
  unsubscribeEvent(e2, t2) {
    this.instanceBridge.apiStore.unsubscribeEvent(e2, t2);
  }
  invokeApi(e2, t2, n2) {
    return this.instanceBridge.callMethod(e2, t2, n2);
  }
  publishEvent(e2, t2) {
    this.instanceBridge.emitEvent(e2, t2);
  }
};
var b = false;
var g = null;
var _ = null;
var v = null;
function y() {
  v && clearTimeout(v), v = setTimeout(() => {
    Math.max(document.body.clientHeight, document.documentElement.offsetHeight);
  }, 200);
}
var m = Object.freeze({
  __proto__: null,
  enableAutoHeight: function() {
    b || (b = true, "undefined" != typeof ResizeObserver ? (_ = new ResizeObserver(() => {
      y();
    }), _.observe(document.documentElement)) : (g = new MutationObserver((e2) => {
      y();
    }), g.observe(document.body, {
      attributes: true,
      childList: true,
      subtree: true
    }), document.addEventListener("animationend", y), document.addEventListener("transitionend", y)));
  },
  disableAutoHeight: function() {
    var e2, t2;
    b && (b = false, null === (e2 = g) || void 0 === e2 || e2.disconnect(), g = null, null === (t2 = _) || void 0 === t2 || t2.disconnect(), _ = null, document.removeEventListener("animationend", y), document.removeEventListener("transitionend", y));
  },
  updateHeight: function(e2) {
  },
  HEIGHT_CHANGED_EVENT: "heightChanged"
});
var k;
function O(e2) {
  if (!k) {
    let t2;
    try {
      t2 = JSON.parse(window.name);
    } catch (e3) {
    }
    if (!t2)
      return;
    k = new f(t2.blockTypeId, t2.channel, e2), window.addEventListener("message", (e3) => {
      const { data: t3, origin: n2 } = e3;
      var i2;
      "object" == typeof (i2 = t3) && "iframe-block" === i2.signature && k.bridge.handleMessage(t3, n2);
    });
  }
  return k;
}
var Client = class {
  constructor() {
    this.clientTimeoutMap = /* @__PURE__ */ new Map();
    this.powerNum = 31;
    this.handleVisibilityChange = () => {
      this.clientTimeoutMap.forEach((_2, requestId) => {
        if (document.visibilityState) {
          this.startLoadTimeout(requestId);
        } else {
          this.stopLoadTimeout(requestId);
        }
      });
    };
    this.handshake = () => __async(this, null, function* () {
      try {
        const { syn } = yield this.invokeAPI({
          scopes: [
            "handshake"
          ],
          params: [],
          config: {}
        });
        if (syn === 1) {
          this.invokeAPI({
            scopes: [
              "handshake"
            ],
            params: [],
            config: {}
          });
        }
      } catch (err) {
        console.warn("handshake error", err);
      }
    });
    this.invokeAPI = (apiData) => __async(this, null, function* () {
      var _a;
      const startTime = Date.now();
      const request = {
        id: this.generateRequestId(),
        body: apiData,
        timestamp: startTime
      };
      const timeout = (_a = apiData.config.timeout) != null ? _a : true;
      const response = timeout === false ? yield this.sendRequest(request) : yield Promise.race([
        this.sendRequest(request),
        this.timeout(request, timeout === true ? 1e3 * 10 : timeout)
      ]);
      this.stopLoadTimeout(request.id, true);
      if (!response) {
        throw new APIInvokeError(ResponseCode.REQUEST_TIMEOUT, apiData.scopes);
      }
      if (!isAPIResponse(response)) {
        throw new APIInvokeError(ResponseCode.RESPONSE_INVALID, apiData.scopes);
      }
      if (response.id !== request.id) {
        throw new APIInvokeError(ResponseCode.REQUEST_INVALID, apiData.scopes);
      }
      const { code, msg, data } = response.body;
      if (code !== ResponseCode.SUCCESS) {
        throw new APIInvokeError(code, apiData.scopes, msg);
      }
      return data;
    });
    this.blockitClient = O({
      env: {
        host: "docx",
        locale: "zh-CN",
        env: "online",
        region: "feishu"
      }
    });
  }
  init() {
    runtime.onInvokeAPI(this.invokeAPI);
    window.addEventListener("visibilitychange", this.handleVisibilityChange);
  }
  destroy() {
    runtime.onInvokeAPI(void 0);
    window.removeEventListener("visibilitychange", this.handleVisibilityChange);
    this.clientTimeoutMap.forEach((timeout) => {
      if (timeout.timer) {
        window.clearTimeout(timeout.timer);
        timeout.timer = null;
      }
    });
    this.clientTimeoutMap.clear();
  }
  initAPI() {
    this.init();
    return DocMiniApp;
  }
  startLoadTimeout(requestId) {
    const timeout = this.clientTimeoutMap.get(requestId);
    if (timeout) {
      if (timeout.timer) {
        window.clearTimeout(timeout.timer);
      }
      timeout.timer = timeout.startTimeout();
      this.clientTimeoutMap.set(requestId, timeout);
    }
  }
  stopLoadTimeout(equestId, isDelete) {
    const timeout = this.clientTimeoutMap.get(equestId);
    if (timeout && timeout.timer) {
      window.clearTimeout(timeout.timer);
      timeout.timer = null;
      this.clientTimeoutMap.set(equestId, timeout);
    }
    isDelete && this.clientTimeoutMap.delete(equestId);
  }
  timeout(request, timeout) {
    return new Promise((_resolve, reject) => {
      this.clientTimeoutMap.set(request.id, {
        startTimeout: () => window.setTimeout(() => {
          reject(new Error(`API[${request.body.scopes.join(".")}] call timeout`));
          this.stopLoadTimeout(request.id, true);
        }, timeout),
        timer: null
      });
      if (document.visibilityState === "visible") {
        this.startLoadTimeout(request.id);
      }
    });
  }
  generateRequestId() {
    return `request-id-${Math.floor(Math.random() * __pow(2, this.powerNum))}`;
  }
};
var API_REQUEST_TYPE = "doc-mini-app-api-request";
var API_RESPONSE_TYPE = "doc-mini-app-api-response";
var IframeClient = class extends Client {
  constructor() {
    super(...arguments);
    this.requestMap = /* @__PURE__ */ new Map();
    this.handleMessage = (message) => {
      this.handleMessageFromHost(message);
    };
  }
  init() {
    super.init();
    window.addEventListener("message", this.handleMessage);
  }
  destroy() {
    this.requestMap.clear();
    window.removeEventListener("message", this.handleMessage);
    super.destroy();
  }
  handleMessageFromHost(message) {
    if (message.data.type !== API_RESPONSE_TYPE) {
      return;
    }
    const response = message.data.data;
    if (!isAPIResponse(response)) {
      return;
    }
    const requestId = response.id;
    const requestItem = this.requestMap.get(requestId);
    if (!requestItem) {
      return;
    }
    this.requestMap.delete(requestId);
    requestItem.resolve(response);
  }
  sendRequestToHost(request) {
    return new Promise((resolve, reject) => {
      var _a;
      const requestId = request.id;
      this.requestMap.set(requestId, {
        request,
        resolve,
        reject
      });
      (_a = window.parent) == null ? void 0 : _a.postMessage({
        type: API_REQUEST_TYPE,
        data: request
      }, "*");
    });
  }
  sendRequest(request) {
    return this.sendRequestToHost(request);
  }
};
var IframeHost = class extends Host {
  constructor() {
    super();
    this.handleMessageFromClient = (message) => __async(this, null, function* () {
      if (message.data.type !== API_REQUEST_TYPE) {
        return;
      }
      const request = message.data.data;
      if (!isAPIRequest(request)) {
        return;
      }
      const requestId = request.id;
      try {
        const postDuration = Date.now() - request.timestamp;
        const source = document.querySelector(`iframe[channel="${message.data.channel}"]`);
        const ctx = this.createContext(request.body, source, postDuration);
        yield this.invokeAPI(ctx);
        this.sendResponseToClient({
          id: requestId,
          body: {
            code: ResponseCode.SUCCESS,
            msg: ResponseMessage[ResponseCode.SUCCESS],
            data: ctx.resData.body
          },
          timestamp: Date.now()
        }, message.source);
      } catch (e2) {
        if (isAPIInvokeError(e2)) {
          this.sendResponseToClient({
            id: requestId,
            body: {
              code: e2.code,
              msg: e2.message,
              data: null
            },
            timestamp: Date.now()
          }, message.source);
        } else {
          this.sendResponseToClient({
            id: requestId,
            body: {
              code: ResponseCode.INTERNAL_ERROR,
              msg: e2.message,
              data: null
            },
            timestamp: Date.now()
          }, message.source);
        }
      }
    });
    this.init();
  }
  init() {
    window.addEventListener("message", this.handleMessageFromClient);
  }
  sendResponseToClient(response, source) {
    source.postMessage({
      type: API_RESPONSE_TYPE,
      data: response
    }, {
      targetOrigin: "*"
    });
  }
  destroy() {
    window.removeEventListener("message", this.handleMessageFromClient);
  }
};
var BlockitClient = class extends IframeClient {
  constructor() {
    super();
    this.Bridge_onDataChange = (handler) => __async(this, null, function* () {
      const index = this.blockEntityListeners.indexOf(handler);
      if (index === -1) {
        this.blockEntityListeners.push(handler);
      }
    });
    this.Bridge_offDataChange = (handler) => __async(this, null, function* () {
      const index = this.blockEntityListeners.indexOf(handler);
      if (index !== -1) {
        this.blockEntityListeners.splice(index, 1);
      }
    });
    this.LifeCycle_onFirstCreated = (handler) => __async(this, null, function* () {
      if (this.isFirstCreated) {
        return;
      }
      const index = this.afterCreateCallback.indexOf(handler);
      if (index === -1) {
        this.afterCreateCallback.push(handler);
      }
    });
    this.LifeCycle_offFirstCreated = (handler) => __async(this, null, function* () {
      const index = this.afterCreateCallback.indexOf(handler);
      if (index !== -1) {
        this.afterCreateCallback.splice(index, 1);
      }
    });
    this.bridgeApiMap = {
      [getAPIKey([
        "Bridge",
        "onDataChange"
      ])]: this.Bridge_onDataChange,
      [getAPIKey([
        "Bridge",
        "offDataChange"
      ])]: this.Bridge_offDataChange,
      [getAPIKey([
        "LifeCycle",
        "onFirstCreated"
      ])]: this.LifeCycle_onFirstCreated,
      [getAPIKey([
        "LifeCycle",
        "offFirstCreated"
      ])]: this.LifeCycle_offFirstCreated
    };
    this.iframeApiMap = {
      [getAPIKey([
        "Block",
        "ImageBlock",
        "insertByUploadImage"
      ])]: true
    };
    this.paramsMap = {
      [getAPIKey([
        "Bridge",
        "updateHeight"
      ])]: (height) => {
        const realHeight = typeof height === "undefined" ? Math.max(document.body.clientHeight, document.documentElement.offsetHeight) : height;
        return [
          realHeight
        ];
      }
    };
    this.blockEntityListeners = [];
    this.handleBlockEntityChange = (blockEntity) => {
      this.blockEntityListeners.forEach((handler) => {
        try {
          handler(blockEntity.data);
        } catch (e2) {
          console.error(e2);
        }
      });
    };
    this.isFirstCreated = false;
    this.afterCreateCallback = [];
    this.handleFirstCreated = (ctx) => __async(this, null, function* () {
      this.afterCreateCallback.forEach((handler) => handler(ctx));
      this.afterCreateCallback = [];
      this.isFirstCreated = true;
    });
    this.idToFunctionMap = {};
    this.handleInvokeFunction = (message) => {
      if (message.data.type !== INVOKE_FUNCTION_TYPE) {
        return;
      }
      const request = message.data.data;
      if (!request || !request.id || !request.args) {
        return;
      }
      const fn = this.idToFunctionMap[request.id];
      if (!fn) {
        return;
      }
      fn(...request.args);
    };
    this.initAPI();
    this.handshake();
  }
  init() {
    super.init();
    this.blockitClient.subscribeEvent("onResourceChange", this.handleBlockEntityChange);
    this.blockitClient.subscribeEvent("firstCreated", this.handleFirstCreated);
  }
  destroy() {
    super.destroy();
    this.blockEntityListeners = [];
    this.afterCreateCallback = [];
    this.blockitClient.unsubscribeEvent("onResourceChange", this.handleBlockEntityChange);
    this.blockitClient.unsubscribeEvent("firstCreated", this.handleFirstCreated);
  }
  generateFunctionId(fn) {
    let id = fn[FUNCTION_ID_KEY];
    if (isFunctionId(id)) {
      return id;
    }
    id = generateFunctionId();
    fn[FUNCTION_ID_KEY] = id;
    this.idToFunctionMap[id] = fn;
    return id;
  }
  handleMessageFromHost(message) {
    switch (message.data.type) {
      case API_RESPONSE_TYPE: {
        super.handleMessageFromHost(message);
        break;
      }
      case INVOKE_FUNCTION_TYPE: {
        this.handleInvokeFunction(message);
        break;
      }
    }
  }
  sendRequest(request) {
    return __async(this, null, function* () {
      var _a, _b;
      const requestId = request.id;
      const { scopes, params } = request.body;
      const APIKey = getAPIKey(scopes);
      try {
        if (this.iframeApiMap[APIKey]) {
          return this.sendRequestToHost(request);
        }
        if (this.bridgeApiMap[APIKey]) {
          const data = yield this.bridgeApiMap[APIKey](...params);
          return {
            id: requestId,
            body: {
              code: ResponseCode.SUCCESS,
              msg: "",
              data
            },
            timestamp: Date.now()
          };
        }
        let transformedParams = params;
        if (this.paramsMap[APIKey]) {
          transformedParams = this.paramsMap[APIKey](...params);
        }
        const newParams = transformedParams.map((param) => typeof param === "function" ? this.generateFunctionId(param) : param);
        const paramObject = paramArrayToParamObject(newParams);
        const { status, errMsg, data: data1 } = yield this.blockitClient.invokeApi(APIKey, paramObject);
        return {
          id: requestId,
          body: {
            code: status !== API_STATUS.SUCCESS ? (data1 == null ? void 0 : data1.code) || ResponseCode.INTERNAL_ERROR : ResponseCode.SUCCESS,
            msg: errMsg,
            data: status !== API_STATUS.SUCCESS ? null : data1
          },
          timestamp: Date.now()
        };
      } catch (e2) {
        if (isAPIInvokeError(e2)) {
          return {
            id: requestId,
            body: {
              code: e2.code,
              msg: e2.message,
              data: null
            },
            timestamp: Date.now()
          };
        } else {
          return {
            id: requestId,
            body: {
              code: ResponseCode.INTERNAL_ERROR,
              msg: (_b = (_a = e2.errMsg) != null ? _a : e2.message) != null ? _b : "",
              data: null
            },
            timestamp: Date.now()
          };
        }
      }
    });
  }
};
var BlockitHost = class extends IframeHost {
  constructor() {
    super(...arguments);
    this.idToFunctionMap = /* @__PURE__ */ new Map();
  }
  generateFunction(id, iframe) {
    let fn = this.idToFunctionMap.get(id);
    if (fn) {
      return fn;
    }
    fn = (...args) => {
      var _a;
      if (!iframe) {
        return;
      }
      const request = {
        id,
        args
      };
      const message = {
        type: INVOKE_FUNCTION_TYPE,
        data: request
      };
      (_a = iframe.contentWindow) == null ? void 0 : _a.postMessage(message, "*");
    };
    this.idToFunctionMap.set(id, fn);
    return fn;
  }
  invokeBlockitAPI(apiKey, context) {
    return __async(this, null, function* () {
      try {
        const api = this.getApi(apiKey);
        if (!api) {
          throw new APIInvokeError(ResponseCode.NOT_IMPLEMENT, []);
        }
        const { scopes } = api;
        const { target } = context;
        const params = paramObjectToParamArray(context.data);
        const newParams = params.map((param) => isFunctionId(param) ? this.generateFunction(param, target.iframe) : param);
        const ctx = this.createContext({
          scopes,
          params: newParams,
          config: {}
        }, target.iframe, context._postDuration);
        yield this.invokeAPI(ctx);
        return {
          status: API_STATUS.SUCCESS,
          errMsg: "",
          data: ctx.resData.body
        };
      } catch (e2) {
        return {
          status: API_STATUS.FAIL,
          errMsg: isAPIInvokeError(e2) ? e2.originMsg : e2.message,
          data: e2
        };
      }
    });
  }
};
var INLINE_VIEW_EVENT;
(function(INLINE_VIEW_EVENT2) {
  INLINE_VIEW_EVENT2["PROPS_UPDATE"] = "inline-view-props-update";
  INLINE_VIEW_EVENT2["INTERACTION_EVENT"] = "inline-view-interaction-event";
})(INLINE_VIEW_EVENT || (INLINE_VIEW_EVENT = {}));
var INLINE_VIEW_INTERACTION_EVENT;
(function(INLINE_VIEW_INTERACTION_EVENT2) {
  INLINE_VIEW_INTERACTION_EVENT2["MOUSE_ENTER"] = "mouseenter";
  INLINE_VIEW_INTERACTION_EVENT2["MOUSE_LEAVE"] = "mouseleave";
  INLINE_VIEW_INTERACTION_EVENT2["MOUSE_OVER"] = "mouseover";
  INLINE_VIEW_INTERACTION_EVENT2["CLICK"] = "click";
})(INLINE_VIEW_INTERACTION_EVENT || (INLINE_VIEW_INTERACTION_EVENT = {}));
var SUPPORT_INTERACTION_EVENT = [
  INLINE_VIEW_INTERACTION_EVENT.MOUSE_ENTER,
  INLINE_VIEW_INTERACTION_EVENT.MOUSE_LEAVE,
  INLINE_VIEW_INTERACTION_EVENT.MOUSE_OVER,
  INLINE_VIEW_INTERACTION_EVENT.CLICK
];
var InlineBlockitClient = class extends BlockitClient {
  constructor() {
    super(...arguments);
    this.typeToHandlerList = /* @__PURE__ */ new Map();
    this.handleInteractionEvent = (context) => {
      const eventType = context.data.type;
      if (SUPPORT_INTERACTION_EVENT.indexOf(eventType) === -1) {
        throw new Error(`[InlineView Interaction] unsupported eventType: ${eventType}`);
      }
      const handlers = this.typeToHandlerList.get(eventType);
      handlers && handlers.forEach((handler) => handler(context.data));
    };
    this.updateInlineView = (data) => {
      this.blockitClient.publishEvent(INLINE_VIEW_EVENT.PROPS_UPDATE, data);
    };
    this.onInteractionEvent = (type, handler) => {
      if (SUPPORT_INTERACTION_EVENT.indexOf(type) === -1) {
        throw new Error(`[InlineView Interaction] unsupported eventType: ${type}`);
      }
      if (!this.typeToHandlerList.has(type)) {
        this.typeToHandlerList.set(type, [
          handler
        ]);
        return;
      }
      const handlerList = this.typeToHandlerList.get(type);
      if (handlerList && handlerList.indexOf(handler) === -1) {
        this.typeToHandlerList.set(type, [
          ...handlerList,
          handler
        ]);
      }
    };
    this.offInteractionEvent = (type, handler) => {
      const handlerList = this.typeToHandlerList.get(type);
      const index = handlerList == null ? void 0 : handlerList.indexOf(handler);
      if (index > -1) {
        handlerList.splice(index, 1);
        this.typeToHandlerList.set(type, handlerList);
      }
    };
    this.publishCustomEvent = (eventType, params) => {
      this.blockitClient.publishEvent(eventType, params);
    };
    this.subscribeCustomEvent = (eventType, handler) => {
      this.blockitClient.subscribeEvent(eventType, handler);
    };
    this.unSubscribeCustomEvent = (eventType, handler) => {
      this.blockitClient.unsubscribeEvent(eventType, handler);
    };
  }
  init() {
    super.init();
    this.blockitClient.subscribeEvent(INLINE_VIEW_EVENT.INTERACTION_EVENT, this.handleInteractionEvent);
  }
  sendRequest(request) {
    return __async(this, null, function* () {
      const forbiddenAPIs = [
        "Bridge_setData",
        "Bridge_getData",
        "Bridge_onDataChange",
        "Bridge_offDataChange",
        "Interaction_getData",
        "Interaction_setData",
        "Interaction_onDataChange",
        "Interaction_offDataChange",
        "Interaction_applyTransaction",
        "Bridge_updateHeight"
      ];
      const requestId = request.id;
      const { scopes } = request.body;
      const APIKey = getAPIKey(scopes);
      if (forbiddenAPIs.includes(APIKey)) {
        return {
          id: requestId,
          body: {
            code: ResponseCode.REQUEST_INVALID,
            msg: `DocMiniApp.${scopes.join(".")} is invalid for inline docMiniApp`,
            data: null
          },
          timestamp: Date.now()
        };
      }
      return __superGet(InlineBlockitClient.prototype, this, "sendRequest").call(this, request);
    });
  }
  destroy() {
    super.destroy();
    this.blockitClient.unsubscribeEvent(INLINE_VIEW_EVENT.INTERACTION_EVENT, this.handleInteractionEvent);
  }
};
export {
  APIInvokeError,
  API_STATUS,
  Block,
  BlockType,
  BlockitClient,
  BlockitHost,
  Bridge,
  ButtonActionType,
  ChatType,
  Client,
  Comment,
  Cookie,
  DOCS_MODE,
  DarkMode,
  DocMiniApp,
  DocsMode,
  Document,
  Env,
  Events,
  FUNCTION_ID_KEY,
  FUNCTION_ID_PREFIX,
  FloatCard,
  Fold,
  Fullscreen,
  HighlightTextColor,
  History,
  Host,
  IFrameBlockType,
  INLINE_VIEW_EVENT,
  INLINE_VIEW_INTERACTION_EVENT,
  INVOKE_FUNCTION_TYPE,
  IframeClient,
  IframeHost,
  ImageBlock,
  InlineBlockitClient,
  InlineComponentTriggerType,
  Interaction,
  InteractionChangesetType,
  Language,
  LifeCycle,
  PRESENTATION_MODE,
  Permission,
  PresentationMode,
  Preview,
  Record,
  ResponseCode,
  ResponseMessage,
  Revision,
  RevisionType,
  RootContainer,
  Runtime,
  Selection,
  Service,
  TableBlock,
  TaskBlockStatus,
  TextBlock,
  TextualBlock,
  User,
  Version,
  View,
  ViewAction,
  Viewport,
  Window,
  apiPermissionSchema,
  generateFunctionId,
  getAPIKey,
  getAllAPIKeys,
  isAPIFunc,
  isAPIInvokeError,
  isAPIRequest,
  isAPIResponse,
  isFunctionId,
  paramArrayToParamObject,
  paramObjectToParamArray,
  runtime
};
