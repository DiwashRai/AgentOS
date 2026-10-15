// The Settings pages' "who saved this" line and admin gate live in the editor kit (editor/save.ts);
// re-exported here for the pages that draw their own save bars.
export { isForbidden, savedLine, type DocumentMeta as SettingsMeta } from "../../editor/save";
