import { describe, expect, it } from "vitest";
import {
  guideDocuments,
  guideHeadings,
  resolveGuideHref,
  resolveGuideImage,
} from "./guideContent";

describe("guide content bundle", () => {
  it("bundles every guide chapter with a unique route", () => {
    expect(guideDocuments).toHaveLength(16);
    expect(new Set(guideDocuments.map((doc) => doc.slug)).size).toBe(
      guideDocuments.length,
    );
    expect(guideDocuments.every((doc) => doc.content.startsWith("# "))).toBe(true);
  });

  it("resolves every relative Markdown link to a guide route", () => {
    for (const document of guideDocuments) {
      const links = [...document.content.matchAll(/\]\(([^)]+\.md(?:#[^)]+)?)\)/g)];
      for (const link of links) {
        expect(
          resolveGuideHref(link[1]),
          `${document.filename} has an unresolved link to ${link[1]}`,
        ).toBeTruthy();
      }
    }
  });

  it("bundles every relative screenshot", () => {
    for (const document of guideDocuments) {
      const images = [...document.content.matchAll(/!\[[^\]]*]\(([^)]+)\)/g)];
      for (const image of images) {
        expect(
          resolveGuideImage(image[1]),
          `${document.filename} has an unresolved image ${image[1]}`,
        ).not.toBe(image[1]);
      }
    }
  });

  it("creates unique in-page anchors per chapter", () => {
    for (const document of guideDocuments) {
      const headings = guideHeadings(document.content);
      expect(
        new Set(headings.map((heading) => heading.id)).size,
        `${document.filename} contains duplicate heading anchors`,
      ).toBe(headings.length);
    }
  });
});
