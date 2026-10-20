package ru.eis223.auditor;
public final class UrlPolicyTest {
    static void check(boolean value) { if(!value) throw new AssertionError(); }
    public static void main(String[] args) {
        check(UrlPolicy.trusted(UrlPolicy.BASE));
        check(UrlPolicy.trusted(UrlPolicy.BASE+"account/thematic?id=123"));
        check(UrlPolicy.trusted("https://WEB-PRODUCTION-1C2EA.UP.RAILWAY.APP:443/"));
        for(String u:new String[]{"http://"+UrlPolicy.HOST,"https://"+UrlPolicy.HOST+".evil.test/",
            "https://"+UrlPolicy.HOST+"@evil.test/","https://evil.test@"+UrlPolicy.HOST+"/",
            "https://"+UrlPolicy.HOST+":9443/","javascript:alert(1)","file:///etc/passwd","content://private","not a URL"}) check(!UrlPolicy.trusted(u));
        check(UrlPolicy.external("https://zakupki.gov.ru/223/"));
        check(!UrlPolicy.external("intent://something"));
        check(!UrlPolicy.external("https://user:password@evil.test/"));
        check(!UrlPolicy.external("javascript:alert(1)"));
        check(UrlPolicy.safeName("../../записка.odt").equals("_.._записка.odt"));
        check(UrlPolicy.safeName("\u0000Файл/PDF:1.pdf").equals("_Файл_PDF_1.pdf"));
        check(!UrlPolicy.safeName("...").isEmpty());
        System.out.println("URL origin, external navigation and document name tests passed");
    }
}
