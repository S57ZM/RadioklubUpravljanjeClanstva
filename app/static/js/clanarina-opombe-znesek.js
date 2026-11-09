(() => {
  const nacini = {
    "Plačana članarina": "klub",
    "Plačana članarina + ZRS": "skupaj",
    "Plačana samo ZRS članarina": "zrs",
    "Plačilo za preteklo leto": "skupaj",
    "Oproščen plačila": "nic"
  };

  const predpomnilnik = new Map();

  const formatZneska = (znesek) =>
    Number(znesek || 0).toFixed(2).replace(".", ",");

  async function pridobiZneske(clanId, leto) {
    const kljuc = `${clanId}-${leto}`;

    if (predpomnilnik.has(kljuc)) {
      return predpomnilnik.get(kljuc);
    }

    const odgovor = await fetch(
      `/placilo/${clanId}/${leto}/zrs-stanje`,
      {
        credentials: "same-origin",
        cache: "no-store"
      }
    );

    if (!odgovor.ok) {
      throw new Error("Zneskov članarine ni mogoče prebrati.");
    }

    const podatki = await odgovor.json();

    const zneski = {
      klub: Number(podatki.klub_znesek || 0),
      zrs: Number(podatki.zrs_znesek || 0),
      skupaj: Number(
        podatki.skupaj ??
        (
          Number(podatki.klub_znesek || 0) +
          Number(podatki.zrs_znesek || 0)
        )
      )
    };

    predpomnilnik.set(kljuc, zneski);
    return zneski;
  }

  async function izpolniZnesek(
    opombaPolje,
    znesekPolje,
    clanId,
    leto
  ) {
    const nacin = nacini[opombaPolje.value.trim()];

    if (!nacin) {
      return;
    }

    if (nacin === "nic") {
      znesekPolje.value = "0,00";
      return;
    }

    try {
      const zneski = await pridobiZneske(clanId, leto);
      znesekPolje.value = formatZneska(zneski[nacin]);
    } catch (napaka) {
      console.error(napaka);
    }
  }

  function poveziObrazec(obrazec) {
    const opomba = obrazec.querySelector(
      'input[name="opombe"][list="clanarina-opombe-predloge"]'
    );

    const znesek = obrazec.querySelector(
      'input[name="znesek"]'
    );

    const clan = obrazec.querySelector(
      'input[name="clan_id"]'
    );

    const leto =
      obrazec.querySelector('input[name="leto"]') ||
      obrazec.querySelector('#urediClanarinaLetoLabel');

    if (!opomba || !znesek || !clan || !leto) {
      return;
    }

    const osvezi = () => {
      if (!leto.value) {
        return;
      }

      izpolniZnesek(
        opomba,
        znesek,
        clan.value,
        leto.value
      );
    };

    opomba.addEventListener("input", osvezi);
    opomba.addEventListener("change", osvezi);
    leto.addEventListener("change", osvezi);
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("form").forEach(poveziObrazec);
  });
})();
